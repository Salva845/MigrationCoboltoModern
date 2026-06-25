"""Transaction processing module (migrated from COBOL BANK74).

Wave 1 / prompt 04: "Process a financial transaction with validation, fee
calculation, and balance update". A self-contained slice that parses an ISO 8583
message, validates the amount against policy thresholds (BR-0011/BR-0012), adds a
fixed fee (BR-0013), checks sufficiency (BR-0014), debits the account atomically
(BR-0015), simulates a credit-state timeout for type 6 (BR-0017) and scans every
account for high-risk balances during the logging state (BR-0018).

This is a *different* functional slice from the BANK85 compliance/fraud flow in
``controller.py``: there is no VAT, the fee is a flat 15.00 and the high-risk
threshold is 90,000.00. The shared account store, ISO parser, state machine and
error codes are reused from ``payment_processing_core`` (DATABASE / ISO-PARSER /
MESSAGES copybooks). The matching core implementation and the cross-service wiring
are handled by sibling prompts 05 and 06.

Monetary maths use :class:`decimal.Decimal` exclusively (no floating point).

BR-0016 (type 4 authorization denial) is intentionally **not** implemented: the
legacy rule is test-only simulation code unsuitable for production and is removed
by this migration (see :func:`_br0016_removed`).
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from payment_processing_core import (
    Account,
    AccountRepository,
    ConcurrencyError,
    ErrorCode,
    Iso8583Message,
    State,
    parse_iso8583,
)
from payment_processing_core.errors import TransactionError

logger = logging.getLogger("payment_message_processing")

_CENTS = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ProcessingConfig:
    """Numeric policy for the BANK74 slice (CONSTANTS.CPY replacement).

    The legacy copybook was not supplied, so the threshold values are documented
    assumptions isolated here for a later equivalence pass; the fixed fee and the
    high-risk threshold are fixed by the prompt.
    """

    # WS-RULE-MIN-AMOUNT / WS-RULE-MAX-TRANS (inclusive bounds, BR-0011/BR-0012).
    min_amount: Decimal = Decimal("1.00")
    max_amount: Decimal = Decimal("1000000.00")
    # Fixed transaction fee added to every debit (BR-0013).
    fixed_fee: Decimal = Decimal("15.00")
    # High-risk balance flag threshold (BR-0018).
    high_risk_threshold: Decimal = Decimal("90000.00")
    # Transaction type that simulates a credit-state timeout (BR-0017).
    timeout_trans_type: int = 6


@dataclass
class ProcessingResult:
    """Outcome of a BANK74 transaction (TransactionResult DTO)."""

    transaction_id: str
    account_id: str
    trans_type: int
    success: bool
    state: State
    error_code: ErrorCode
    original_amount: Decimal
    fee: Decimal = Decimal("0.00")
    total_debit: Decimal = Decimal("0.00")
    updated_balance: Decimal | None = None
    high_risk_accounts: list[str] = field(default_factory=list)
    message: str = ""
    duplicate: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TransactionProcessor:
    """Validation + fee calculation + balance update pipeline (BANK74)."""

    def __init__(
        self,
        repository: AccountRepository,
        config: ProcessingConfig | None = None,
    ):
        self.repository = repository
        self.config = config or ProcessingConfig()
        self._lock = threading.Lock()
        self._log: list[ProcessingResult] = []
        self._by_id: dict[str, ProcessingResult] = {}

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def process(self, transaction_id: str, message: dict | Iso8583Message) -> ProcessingResult:
        # Idempotency: a repeated transaction id returns the cached result and
        # never double-debits (retry safety).
        cached = self._by_id.get(transaction_id)
        if cached is not None:
            logger.info("idempotent replay for transaction %s", transaction_id)
            replay = ProcessingResult(**vars(cached))
            replay.duplicate = True
            return replay

        try:
            iso = message if isinstance(message, Iso8583Message) else parse_iso8583(message)
        except TransactionError as exc:
            return self._finish(
                self._fail(transaction_id, "?", 0, Decimal("0.00"), exc.code, exc.reason)
            )

        try:
            result = self._run(transaction_id, iso)
        except _ProcessingStop as stop:
            result = self._fail(
                transaction_id,
                iso.account_id,
                iso.trans_type,
                iso.amount,
                stop.code,
                stop.reason,
            )
        except Exception as exc:  # noqa: BLE001 - map any unexpected error, never leak.
            logger.exception("unexpected error processing %s", transaction_id)
            result = self._fail(
                transaction_id,
                iso.account_id,
                iso.trans_type,
                iso.amount,
                ErrorCode.SYSTEM_ERROR,
                str(exc),
            )
        return self._finish(result)

    # ------------------------------------------------------------------ #
    # Pipeline (state order: 05 VAL -> 20 DEBIT -> 30 CREDIT, errors -> 40 LOG)
    # ------------------------------------------------------------------ #
    def _run(self, transaction_id: str, iso: Iso8583Message) -> ProcessingResult:
        account = self.repository.find_by_account_id(iso.account_id)
        if account is None:
            raise _ProcessingStop(
                ErrorCode.ACCOUNT_NOT_FOUND, f"account {iso.account_id} not found"
            )

        # State 05 (VAL): amount thresholds, minimum before maximum.
        self.br0011_minimum_amount(iso.amount)
        self.br0012_maximum_amount(iso.amount)

        # State 20 (DEBIT): fee-inclusive total, then sufficiency, then debit.
        total_debit = self.br0013_total_debit(iso.amount)
        self.br0014_sufficient_funds(account, total_debit)

        # State 30 (CREDIT): type 6 simulates a timeout before the irreversible
        # write, so the balance is left untouched (BR-0017 / TC-0026).
        if iso.trans_type == self.config.timeout_trans_type:
            raise _ProcessingStop(
                ErrorCode.TIMEOUT,
                f"timeout simulated for type {iso.trans_type} (balance not debited)",
            )

        updated = self.br0015_debit_account(account, total_debit)
        result = ProcessingResult(
            transaction_id=transaction_id,
            account_id=account.account_id,
            trans_type=iso.trans_type,
            success=True,
            state=State.CREDIT,
            error_code=ErrorCode.OK,
            original_amount=_money(iso.amount),
            fee=_money(self.config.fixed_fee),
            total_debit=total_debit,
            updated_balance=updated.balance,
            message="transaction posted",
        )
        logger.info(
            "transaction %s posted: account=%s amount=%s fee=%s total=%s balance=%s state=%s",
            transaction_id,
            account.account_id,
            result.original_amount,
            result.fee,
            total_debit,
            updated.balance,
            int(State.CREDIT),
        )
        return result

    # ------------------------------------------------------------------ #
    # Business rules (discrete, testable)
    # ------------------------------------------------------------------ #
    def br0011_minimum_amount(self, amount: Decimal) -> None:
        """BR-0011: reject amounts below WS-RULE-MIN-AMOUNT."""
        if amount < self.config.min_amount:
            raise _ProcessingStop(
                ErrorCode.INVALID_FORMAT,
                f"amount {amount} below minimum {self.config.min_amount}",
            )

    def br0012_maximum_amount(self, amount: Decimal) -> None:
        """BR-0012: reject amounts above WS-RULE-MAX-TRANS."""
        if amount > self.config.max_amount:
            raise _ProcessingStop(
                ErrorCode.INVALID_FORMAT,
                f"amount {amount} above maximum {self.config.max_amount}",
            )

    def br0013_total_debit(self, amount: Decimal) -> Decimal:
        """BR-0013: total debit = transaction amount + fixed 15.00 fee."""
        return _money(amount + self.config.fixed_fee)

    def br0014_sufficient_funds(self, account: Account, total_debit: Decimal) -> None:
        """BR-0014: balance must cover the fee-inclusive total (no overdraft)."""
        if account.balance < total_debit:
            raise _ProcessingStop(
                ErrorCode.INSUFF_FUNDS,
                f"insufficient funds: balance {account.balance} < {total_debit} (not debited)",
            )

    def br0015_debit_account(self, account: Account, total_debit: Decimal) -> Account:
        """BR-0015: atomically subtract the total debit from the balance."""
        try:
            return self.repository.update_balance(
                account.account_id, account.balance - total_debit, account.version
            )
        except (ConcurrencyError, KeyError) as exc:
            raise _ProcessingStop(
                ErrorCode.SYSTEM_ERROR, f"balance update failed, rolled back: {exc}"
            ) from exc

    def br0018_high_risk_scan(self) -> list[str]:
        """BR-0018: flag every account whose balance exceeds the threshold.

        Compliance scan over all accounts; informational only and never blocks a
        transaction. Runs during the logging state regardless of the outcome.
        """
        flagged = [
            a.account_id
            for a in self.repository.all_accounts()
            if a.balance > self.config.high_risk_threshold
        ]
        for account_id in flagged:
            logger.info("high-risk account flagged: %s", account_id)
        return flagged

    @staticmethod
    def _br0016_removed() -> None:
        """BR-0016 (type 4 authorization denial) — REMOVED.

        Legacy test-only simulation: in BANK74 a transaction type of 4 was
        rejected to exercise the auth path. It is non-functional in production and
        is deliberately omitted from this migration. Kept as a documentation
        marker only; it is never called.
        """

    # ------------------------------------------------------------------ #
    # Logging state (40) + audit trail
    # ------------------------------------------------------------------ #
    def _finish(self, result: ProcessingResult) -> ProcessingResult:
        # State 40 (LOG): run the high-risk scan after the outcome is known so the
        # flags are part of the persisted transaction log, then append the entry.
        result.high_risk_accounts = self.br0018_high_risk_scan()
        with self._lock:
            if result.transaction_id not in self._by_id:
                self._log.append(result)
                self._by_id[result.transaction_id] = result
        return result

    def _fail(
        self,
        transaction_id: str,
        account_id: str,
        trans_type: int,
        amount: Decimal,
        code: ErrorCode,
        reason: str,
    ) -> ProcessingResult:
        logger.warning(
            "transaction %s rejected: code=%s reason=%s state=%s",
            transaction_id,
            code.value,
            reason,
            int(State.AUDIT),
        )
        return ProcessingResult(
            transaction_id=transaction_id,
            account_id=account_id,
            trans_type=trans_type,
            success=False,
            state=State.AUDIT,  # state 40 (LOG) for every error path
            error_code=code,
            original_amount=_money(amount),
            message=reason,
        )

    @property
    def entries(self) -> list[ProcessingResult]:
        """Immutable view of the audit trail (most recent last)."""
        with self._lock:
            return list(self._log)


class _ProcessingStop(Exception):
    """Internal short-circuit carrying the error code + audit reason."""

    def __init__(self, code: ErrorCode, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason
