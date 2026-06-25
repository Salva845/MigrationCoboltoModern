"""Acceptance + unit tests for the BANK74 processing slice (TC-0019..TC-0027).

Covers prompt 05 in payment-processing-core: amount-threshold validation
(BR-0011/BR-0012), fixed 15.00 fee (BR-0013), insufficient-funds check
(BR-0014), atomic debit (BR-0015), high-risk account scan (BR-0018), type-6
timeout simulation (BR-0017), and the removal of the type-4 authorization denial
(BR-0016). Monetary assertions use Decimal exclusively.
"""

import threading
from decimal import Decimal

from payment_processing_core import (
    Account,
    ErrorCode,
    InMemoryAccountRepository,
    ProcessingConfig,
    State,
    TransactionProcessor,
)

# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
FEE = Decimal("15.00")
HIGH_RISK = Decimal("90000.00")


def acc(account_id="ACC1", status="A", balance="1000.00", risk=0, acc_type="CHECKING", version=0):
    return Account(
        account_id=account_id,
        status=status,
        balance=Decimal(balance),
        risk_level=risk,
        account_type=acc_type,
        version=version,
    )


def minor(amount: str) -> str:
    """Major units -> ISO 8583 DE004 minor units (cents) as a digit string."""
    return str(int((Decimal(amount) * 100).to_integral_value()))


def message(amount="100.00", trans_type=0, account_id="ACC1") -> dict:
    return {"4": minor(amount), "3": trans_type, "102": account_id}


def build_processor(accounts=None, config=None) -> TransactionProcessor:
    return TransactionProcessor(
        repository=InMemoryAccountRepository(accounts if accounts is not None else [acc()]),
        config=config,
    )


def balance_of(processor: TransactionProcessor, account_id="ACC1") -> Decimal:
    return processor.repository.find_by_account_id(account_id).balance


# --------------------------------------------------------------------------- #
# TC-0019 — happy path
# --------------------------------------------------------------------------- #
def test_tc0019_happy_path_fee_and_balance_update():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("T19", message(amount="100.00", trans_type=0))

    assert result.success is True
    assert result.error_code is ErrorCode.OK
    assert result.state == State.CREDIT == 30
    assert result.fee == FEE
    assert result.total_debit == Decimal("115.00")
    assert result.updated_balance == Decimal("885.00")
    assert balance_of(proc) == Decimal("885.00")


# --------------------------------------------------------------------------- #
# TC-0020 / TC-0021 / TC-0022 — amount thresholds
# --------------------------------------------------------------------------- #
def test_tc0020_amount_equals_minimum_is_accepted():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("T20", message(amount="1.00"))

    assert result.success is True
    assert result.total_debit == Decimal("16.00")
    assert balance_of(proc) == Decimal("984.00")


def test_tc0021_amount_equals_maximum_is_accepted_above_is_rejected():
    proc = build_processor([acc(balance="2000000.00")])
    at_max = proc.process("T21a", message(amount="1000000.00"))
    assert at_max.success is True

    proc2 = build_processor([acc(balance="2000000.00")])
    over = proc2.process("T21b", message(amount="1000000.01"))
    assert over.success is False
    assert over.error_code is ErrorCode.INVALID_FORMAT
    assert over.state == State.AUDIT == 40
    assert balance_of(proc2) == Decimal("2000000.00")  # untouched


def test_tc0022_amount_below_minimum_is_rejected():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("T22", message(amount="0.99"))

    assert result.success is False
    assert result.error_code is ErrorCode.INVALID_FORMAT
    assert result.state == State.AUDIT
    assert balance_of(proc) == Decimal("1000.00")  # untouched


def test_minimum_is_validated_before_maximum():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("Tord", message(amount="0.00"))
    assert result.error_code is ErrorCode.INVALID_FORMAT
    assert "below minimum" in result.message


# --------------------------------------------------------------------------- #
# TC-0023 / TC-0024 — insufficient funds boundary
# --------------------------------------------------------------------------- #
def test_tc0023_balance_equals_total_debit_succeeds_to_zero():
    # balance == amount + 15.00 fee -> accepted, balance reduced to zero.
    proc = build_processor([acc(balance="115.00")])
    result = proc.process("T23", message(amount="100.00"))

    assert result.success is True
    assert result.total_debit == Decimal("115.00")
    assert result.updated_balance == Decimal("0.00")
    assert balance_of(proc) == Decimal("0.00")


def test_tc0024_balance_below_total_debit_is_rejected():
    proc = build_processor([acc(balance="114.99")])
    result = proc.process("T24", message(amount="100.00"))

    assert result.success is False
    assert result.error_code is ErrorCode.INSUFF_FUNDS
    assert result.state == State.AUDIT
    assert balance_of(proc) == Decimal("114.99")  # not debited


# --------------------------------------------------------------------------- #
# TC-0025 — high-risk account detection during logging state
# --------------------------------------------------------------------------- #
def test_tc0025_high_risk_accounts_flagged_in_log_output():
    proc = build_processor(
        [
            acc(account_id="ACC1", balance="1000.00"),
            acc(account_id="RICH", balance="95000.00"),  # > 90,000.00
            acc(account_id="EDGE", balance="90000.00"),  # == threshold, not flagged
        ]
    )
    result = proc.process("T25", message(amount="100.00", account_id="ACC1"))

    assert result.success is True
    assert result.high_risk_accounts == ["RICH"]
    # The flags are part of the persisted audit entry.
    assert proc.entries[-1].high_risk_accounts == ["RICH"]


def test_high_risk_threshold_is_strict_greater_than():
    # An account exactly at the threshold is never flagged (> not >=).
    proc = build_processor(
        [acc(account_id="ACC1", balance="1000.00"), acc(account_id="EDGE", balance="90000.00")]
    )
    result = proc.process("Tedge", message(amount="100.00", account_id="ACC1"))
    assert result.high_risk_accounts == []


def test_high_risk_scan_runs_on_failure_too():
    proc = build_processor(
        [acc(account_id="ACC1", balance="5.00"), acc(account_id="RICH", balance="95000.00")]
    )
    result = proc.process("T25b", message(amount="100.00", account_id="ACC1"))
    assert result.success is False  # insufficient funds
    assert result.high_risk_accounts == ["RICH"]


# --------------------------------------------------------------------------- #
# TC-0026 — timeout during credit processing (type 6)
# --------------------------------------------------------------------------- #
def test_tc0026_type6_timeout_does_not_debit():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("T26", message(amount="100.00", trans_type=6))

    assert result.success is False
    assert result.error_code is ErrorCode.TIMEOUT
    assert result.state == State.AUDIT == 40
    assert balance_of(proc) == Decimal("1000.00")  # not modified


# --------------------------------------------------------------------------- #
# TC-0027 — equivalence with the legacy oracle for a representative transaction
# --------------------------------------------------------------------------- #
def test_tc0027_equivalence_with_legacy_oracle():
    # BANK74.CBL was not supplied; the oracle below is the documented legacy
    # output for the representative input (amount 250.00, type 0, balance
    # 5,000.00): fee 15.00, total debit 265.00, new balance 4,735.00, state 30.
    proc = build_processor([acc(balance="5000.00")])
    result = proc.process("T27", message(amount="250.00", trans_type=0))

    legacy_oracle = {
        "status": ErrorCode.OK.value,
        "state": 30,
        "fee": "15.00",
        "total_debit": "265.00",
        "updated_balance": "4735.00",
    }
    body = result.to_dict()
    assert {k: body[k] for k in legacy_oracle} == legacy_oracle


# --------------------------------------------------------------------------- #
# BR-0016 removal — type 4 is no longer rejected
# --------------------------------------------------------------------------- #
def test_br0016_removed_type4_is_processed_normally():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("T4", message(amount="100.00", trans_type=4))

    assert result.success is True
    assert result.error_code is ErrorCode.OK
    assert balance_of(proc) == Decimal("885.00")


# --------------------------------------------------------------------------- #
# Exception paths: account not found, malformed message
# --------------------------------------------------------------------------- #
def test_account_not_found_is_rejected():
    proc = build_processor([acc(account_id="OTHER", balance="1000.00")])
    result = proc.process("Tnf", message(amount="100.00", account_id="MISSING"))
    assert result.success is False
    assert result.error_code is ErrorCode.ACCOUNT_NOT_FOUND
    assert result.state == State.AUDIT


def test_malformed_message_is_rejected():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("Tbad", {"3": 0, "102": "ACC1"})  # no amount (DE004)
    assert result.success is False
    assert result.error_code is ErrorCode.INVALID_FORMAT


# --------------------------------------------------------------------------- #
# Idempotency — duplicate transaction id never double-debits
# --------------------------------------------------------------------------- #
def test_idempotent_replay_does_not_double_debit():
    proc = build_processor([acc(balance="1000.00")])
    first = proc.process("DUP", message(amount="100.00"))
    second = proc.process("DUP", message(amount="100.00"))

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.updated_balance == first.updated_balance
    assert balance_of(proc) == Decimal("885.00")  # debited exactly once
    assert len(proc.entries) == 1


# --------------------------------------------------------------------------- #
# Concurrency — no lost updates / double-debit under load
# --------------------------------------------------------------------------- #
def test_concurrent_debits_have_no_lost_updates():
    proc = build_processor([acc(balance="100000.00")])
    n = 30
    results: list = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        r = proc.process(f"C{i}", message(amount="100.00"))
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [r for r in results if r.success]
    # total debit = 115.00 per success; balance reflects exactly that many.
    expected = Decimal("100000.00") - Decimal("115.00") * len(succeeded)
    assert balance_of(proc) == expected
    assert balance_of(proc) >= Decimal("0.00")


# --------------------------------------------------------------------------- #
# Numeric precision — fixed-point, 2 decimals, no float drift
# --------------------------------------------------------------------------- #
def test_amounts_are_fixed_point_two_decimals():
    proc = build_processor([acc(balance="1000.00")])
    result = proc.process("Tprec", message(amount="100.10"))
    assert result.total_debit == Decimal("115.10")
    assert result.updated_balance == Decimal("884.90")
    body = result.to_dict()
    assert body["total_debit"] == "115.10"
    assert body["updated_balance"] == "884.90"


# --------------------------------------------------------------------------- #
# Config — thresholds are not hard-coded in the logic
# --------------------------------------------------------------------------- #
def test_config_overrides_are_respected():
    cfg = ProcessingConfig(fixed_fee=Decimal("5.00"), high_risk_threshold=Decimal("500.00"))
    proc = build_processor([acc(balance="1000.00")], config=cfg)
    result = proc.process("Tcfg", message(amount="100.00"))
    assert result.fee == Decimal("5.00")
    assert result.total_debit == Decimal("105.00")
    assert result.high_risk_accounts == ["ACC1"]  # 895 > 500 after debit


# --------------------------------------------------------------------------- #
# Business-rule order: fee added before sufficiency check (BR-0013 before BR-0014)
# --------------------------------------------------------------------------- #
def test_fee_added_before_sufficiency_check():
    # balance covers the amount but not the amount + fee -> rejected.
    proc = build_processor([acc(balance="100.00")])
    result = proc.process("Tfee", message(amount="100.00"))
    assert result.success is False
    assert result.error_code is ErrorCode.INSUFF_FUNDS
    assert balance_of(proc) == Decimal("100.00")  # untouched
