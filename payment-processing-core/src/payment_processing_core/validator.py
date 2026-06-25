"""Core transaction validation and processing pipeline (BANK85 replacement).

Orchestrates ISO 8583 parsing, the ten business rules (BR-0001..BR-0010), fee and
tax calculation, account debit, state transitions, and audit logging. Monetary
maths use :class:`decimal.Decimal` exclusively (no floating point).
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

from .config import RuleConfig
from .errors import ErrorCode, TransactionError
from .fraud import DefaultFraudScoringEngine, FraudScoringEngine
from .iso8583 import Iso8583Message, parse_iso8583
from .log_engine import TransactionLogEngine
from .models import Account, State, TransactionResult
from .repository import AccountRepository, ConcurrencyError

logger = logging.getLogger("payment_processing_core")

_CENTS = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


class TransactionValidator:
    def __init__(
        self,
        repository: AccountRepository,
        log_engine: TransactionLogEngine,
        fraud_engine: FraudScoringEngine | None = None,
        config: RuleConfig | None = None,
    ):
        self.repository = repository
        self.log_engine = log_engine
        self.config = config or RuleConfig()
        self.fraud_engine = fraud_engine or DefaultFraudScoringEngine(self.config)

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def process(
        self,
        transaction_id: str,
        message: dict | Iso8583Message,
        employee_id: int | None = None,
    ) -> TransactionResult:
        # Idempotency: a repeated transaction id returns the cached result and
        # never double-debits.
        cached = self.log_engine.get(transaction_id)
        if cached is not None:
            logger.info("idempotent replay for transaction %s", transaction_id)
            return cached

        try:
            iso = message if isinstance(message, Iso8583Message) else parse_iso8583(message)
        except TransactionError as exc:
            return self._fail(transaction_id, "?", 0, Decimal("0.00"), exc, State.VALIDATE)

        try:
            result = self._run_pipeline(transaction_id, iso, employee_id)
        except TransactionError as exc:
            result = self._fail(
                transaction_id,
                iso.account_id,
                iso.trans_type,
                iso.amount,
                exc,
                exc.audit_state or State.VALIDATE,
            )
        except Exception as exc:  # noqa: BLE001 - map any unexpected error, never leak.
            logger.exception("unexpected error processing %s", transaction_id)
            result = self._fail(
                transaction_id,
                iso.account_id,
                iso.trans_type,
                iso.amount,
                TransactionError(ErrorCode.SYSTEM_ERROR, str(exc)),
                State.VALIDATE,
            )

        self.log_engine.log_transaction(result)
        return result

    # ------------------------------------------------------------------ #
    # Pipeline (order is significant - see prompt section 8)
    # ------------------------------------------------------------------ #
    def _run_pipeline(
        self, transaction_id: str, iso: Iso8583Message, employee_id: int | None = None
    ) -> TransactionResult:
        # Step 3: account lookup.
        account = self.repository.find_by_account_id(iso.account_id)
        if account is None:
            raise TransactionError(
                ErrorCode.ACCOUNT_NOT_FOUND, f"account {iso.account_id} not found"
            )

        # Validation state (3200).
        self.br0001_blacklist(account)
        self.br0002_minimum_amount(iso.amount)
        self.br0003_maximum_amount(iso.amount)
        self.br0004_account_type_restriction(account, iso.trans_type)

        # Financial calculations (4500).
        fee = self.br0006_spei_fee(iso.trans_type)
        vat, final_amount = self.br0007_vat_and_total(iso.amount, fee)

        # Fraud heuristics (8500), then insufficient-funds (3400), then debit.
        self.br0008_fraud_scoring(iso.amount, account.risk_level)
        self.br0005_insufficient_funds(account, final_amount)

        # BR-0009: payroll employee parity (only when a payroll context supplies
        # an employee id; non-payroll transactions skip this rule).
        if employee_id is not None and self.br0009_validate_employee(employee_id) == "P":
            raise TransactionError(
                ErrorCode.AUTH_DENIED,
                f"payroll blocked: employee {employee_id} has an even id (status 'P')",
            )

        # Step 11/12: atomic debit + transition to credit state.
        try:
            self.repository.update_balance(
                account.account_id, account.balance - final_amount, account.version
            )
        except (ConcurrencyError, KeyError) as exc:
            raise TransactionError(
                ErrorCode.SYSTEM_ERROR, f"balance update failed, rolled back: {exc}"
            ) from exc

        result = TransactionResult(
            transaction_id=transaction_id,
            account_id=account.account_id,
            trans_type=iso.trans_type,
            success=True,
            state=State.CREDIT,
            error_code=ErrorCode.OK,
            original_amount=_money(iso.amount),
            fee=fee,
            vat=vat,
            final_amount=final_amount,
            message="transaction posted",
        )
        logger.info(
            "transaction %s posted: account=%s amount=%s fee=%s vat=%s final=%s state=%s",
            transaction_id,
            account.account_id,
            result.original_amount,
            fee,
            vat,
            final_amount,
            int(State.CREDIT),
        )
        return result

    # ------------------------------------------------------------------ #
    # Business rules (discrete, testable)
    # ------------------------------------------------------------------ #
    def br0001_blacklist(self, account: Account) -> None:
        if account.status == "B":
            raise TransactionError(
                ErrorCode.BLACKLISTED,
                f"blacklist: account {account.account_id} status 'B'",
                audit_state=State.AUDIT,
            )

    def br0002_minimum_amount(self, amount: Decimal) -> None:
        if amount < self.config.min_amount:
            raise TransactionError(
                ErrorCode.INVALID_FORMAT,
                f"amount {amount} below minimum {self.config.min_amount}",
            )

    def br0003_maximum_amount(self, amount: Decimal) -> None:
        if amount > self.config.max_amount:
            raise TransactionError(
                ErrorCode.INVALID_FORMAT,
                f"amount {amount} above maximum {self.config.max_amount}",
            )

    def br0004_account_type_restriction(self, account: Account, trans_type: int) -> None:
        if account.account_type == "NOMINA" and trans_type == self.config.spei_trans_type:
            raise TransactionError(
                ErrorCode.AUTH_DENIED, "SPEI transfer from NOMINA (payroll) account is restricted"
            )

    def br0005_insufficient_funds(self, account: Account, final_amount: Decimal) -> None:
        if account.balance < final_amount:
            raise TransactionError(
                ErrorCode.INSUFF_FUNDS,
                f"insufficient funds: balance {account.balance} < {final_amount} (not debited)",
                audit_state=State.AUDIT,
            )

    def br0006_spei_fee(self, trans_type: int) -> Decimal:
        if trans_type == self.config.spei_trans_type:
            return _money(self.config.spei_fee)
        return _money(Decimal("0"))

    def br0007_vat_and_total(self, amount: Decimal, fee: Decimal) -> tuple[Decimal, Decimal]:
        # VAT is charged on (amount + fee); no compounding. Final = amount + fee + VAT.
        vat = _money((amount + fee) * self.config.vat_rate)
        final_amount = _money(amount + fee + vat)
        return vat, final_amount

    def br0008_fraud_scoring(self, amount: Decimal, risk_level: int) -> None:
        score = self.fraud_engine.score(amount, risk_level)
        threshold = self.config.fraud_block_threshold
        blocked = score > threshold if self.config.fraud_block_strict else score >= threshold
        if blocked:
            op = ">" if self.config.fraud_block_strict else ">="
            raise TransactionError(
                ErrorCode.BLACKLISTED, f"fraud alert: fraud score {score} {op} {threshold}"
            )

    def br0009_validate_employee(self, emp_id: int | None) -> str:
        """Return PR-EMP-STATUS: 'P' (blocked) for even ids, 'V' (valid) otherwise."""
        if emp_id is None:
            raise TransactionError(ErrorCode.INVALID_FORMAT, "missing employee id")
        if emp_id % 2 == 0:
            logger.info("payroll blocked: employee %s has an even id", emp_id)
            return "P"
        return "V"

    def br0010_high_balance_report(self) -> list[Account]:
        """Flag accounts whose balance exceeds the high-balance threshold.

        Informational only (no blocking); intended as an off-critical-path batch.
        """
        flagged = [
            a
            for a in self.repository.all_accounts()
            if a.balance > self.config.high_balance_threshold
        ]
        for a in flagged:
            logger.info("high-balance risk: account %s balance %s", a.account_id, a.balance)
        return flagged

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _fail(
        self,
        transaction_id: str,
        account_id: str,
        trans_type: int,
        amount: Decimal,
        exc: TransactionError,
        state: int,
    ) -> TransactionResult:
        logger.warning(
            "transaction %s rejected: code=%s reason=%s state=%s",
            transaction_id,
            exc.code.value,
            exc.reason,
            int(state),
        )
        return TransactionResult(
            transaction_id=transaction_id,
            account_id=account_id,
            trans_type=trans_type,
            success=False,
            state=State(state),
            error_code=exc.code,
            original_amount=_money(amount),
            message=exc.reason,
        )
