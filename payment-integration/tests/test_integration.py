"""Wave-1 end-to-end integration tests (TC-0001..TC-0018).

Every case drives the *real* HTTP boundary exposed by payment-message-processing
(POST /transactions/validate) which delegates to payment-processing-core, then
asserts the cross-service contract: HTTP status <- error-code mapping, numeric
calculations, state-machine transitions, atomic balance updates observed on the
shared repository, audit-log completeness, and idempotency across two requests.

No business logic is duplicated here; the harness only wires the two slices.
"""

import threading
from decimal import Decimal

from payment_processing_core import ErrorCode, FraudScoringEngine, State

from payment_integration import account, request_payload, running_system

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class StubFraud(FraudScoringEngine):
    """Deterministic score so fraud-boundary tests do not depend on heuristics."""

    def __init__(self, value: int):
        self.value = value

    def score(self, amount, risk_level):
        return self.value


# --------------------------------------------------------------------------- #
# TC-0001 happy path (end-to-end over HTTP)
# --------------------------------------------------------------------------- #
def test_tc0001_happy_path_end_to_end():
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00", trans_type=0))

    assert status == 200
    assert body["success"] is True
    assert body["status"] == "WS-OK"
    assert body["state"] == int(State.CREDIT) == 30
    # fee 0, vat = 100 * 0.16 = 16.00, final = 116.00
    assert body["fee"] == "0.00"
    assert body["vat"] == "16.00"
    assert body["final_amount"] == "116.00"


# --------------------------------------------------------------------------- #
# TC-0002..TC-0005 amount thresholds (inclusive on the passing side)
# --------------------------------------------------------------------------- #
def test_tc0002_amount_at_minimum_passes():
    with running_system() as sys:
        status, body = sys.post_validate(request_payload(amount="1.00"))
    assert status == 200
    assert body["success"] is True


def test_tc0003_amount_below_minimum_rejected():
    with running_system() as sys:
        status, body = sys.post_validate(request_payload(amount="0.99"))
    assert status == 400
    assert body["error_code"] == "WS-ERR-INVALID-FORMAT"
    assert body["state"] == int(State.VALIDATE)


def test_tc0004_amount_at_maximum_passes():
    with running_system([account(balance="2000000.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="1000000.00"))
    assert status == 200
    assert body["success"] is True


def test_tc0005_amount_above_maximum_rejected():
    with running_system([account(balance="2000000.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="1000000.01"))
    assert status == 400
    assert body["error_code"] == "WS-ERR-INVALID-FORMAT"


# --------------------------------------------------------------------------- #
# TC-0006..TC-0007 fraud boundary (strict > 60 per prompt 03 gotcha)
# --------------------------------------------------------------------------- #
def test_tc0006_fraud_score_exactly_60_allows():
    with running_system([account(balance="1000.00")], fraud=StubFraud(60)) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00"))
    assert status == 200
    assert body["success"] is True


def test_tc0007_fraud_score_59_allows():
    with running_system([account(balance="1000.00")], fraud=StubFraud(59)) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00"))
    assert status == 200
    assert body["success"] is True


def test_fraud_score_above_60_blocks():
    with running_system([account(balance="1000.00")], fraud=StubFraud(61)) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00"))
    assert status == 403
    assert body["error_code"] == "WS-ERR-BLACKLISTED"


# --------------------------------------------------------------------------- #
# TC-0008 SPEI fee for type 2 vs non-SPEI
# --------------------------------------------------------------------------- #
def test_tc0008_spei_fee_only_for_type_2():
    with running_system([account(balance="1000.00")]) as sys:
        _, spei = sys.post_validate(request_payload(txn_id="S", amount="100.00", trans_type=2))
        _, other = sys.post_validate(request_payload(txn_id="O", amount="100.00", trans_type=1))
    # SPEI: fee 12.50, vat = (100 + 12.50) * 0.16 = 18.00, final = 130.50
    assert spei["fee"] == "12.50"
    assert spei["vat"] == "18.00"
    assert spei["final_amount"] == "130.50"
    # Non-SPEI: no fee
    assert other["fee"] == "0.00"
    assert other["final_amount"] == "116.00"


# --------------------------------------------------------------------------- #
# TC-0009..TC-0010 insufficient-funds boundary
# --------------------------------------------------------------------------- #
def test_tc0009_balance_exactly_sufficient_passes():
    # amount 100 -> final 116.00; balance exactly 116.00 succeeds.
    with running_system([account(balance="116.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00"))
        assert status == 200
        assert body["success"] is True
        assert sys.account_repository.find_by_account_id("ACC1").balance == Decimal("0.00")


def test_tc0010_balance_one_unit_below_final_rejected():
    with running_system([account(balance="115.99")]) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00"))
        assert status == 409
        assert body["error_code"] == "WS-ERR-INSUFF-FUNDS"
        assert body["state"] == int(State.AUDIT) == 40
        # not debited
        assert sys.account_repository.find_by_account_id("ACC1").balance == Decimal("115.99")


# --------------------------------------------------------------------------- #
# TC-0011 blacklist -> audit state
# --------------------------------------------------------------------------- #
def test_tc0011_blacklist_rejected_audit_state():
    with running_system([account(status="B", balance="1000.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00"))
        assert status == 403
        assert body["error_code"] == "WS-ERR-BLACKLISTED"
        assert body["state"] == int(State.AUDIT)
        # not debited
        assert sys.account_repository.find_by_account_id("ACC1").balance == Decimal("1000.00")


# --------------------------------------------------------------------------- #
# TC-0012..TC-0013 NOMINA SPEI restriction
# --------------------------------------------------------------------------- #
def test_tc0012_spei_from_nomina_rejected():
    with running_system([account(acc_type="NOMINA", balance="1000.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00", trans_type=2))
        assert status == 403
        assert body["error_code"] == "WS-ERR-AUTH-DENIED"


def test_tc0013_non_spei_from_nomina_allowed():
    with running_system([account(acc_type="NOMINA", balance="1000.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00", trans_type=1))
        assert status == 200
        assert body["success"] is True


# --------------------------------------------------------------------------- #
# TC-0014 VAT / total formula: final = amount + fee + (amount + fee) * rate
# --------------------------------------------------------------------------- #
def test_tc0014_vat_formula_multiple_cases():
    cases = [
        # (amount, trans_type, expected_fee, expected_vat, expected_final)
        ("100.00", 0, "0.00", "16.00", "116.00"),
        ("100.00", 2, "12.50", "18.00", "130.50"),
        ("12345.67", 0, "0.00", "1975.31", "14320.98"),  # half-up rounding
    ]
    with running_system([account(balance="1000000.00")]) as sys:
        for i, (amount, ttype, fee, vat, final) in enumerate(cases):
            _, body = sys.post_validate(
                request_payload(txn_id=f"V{i}", amount=amount, trans_type=ttype)
            )
            assert body["fee"] == fee
            assert body["vat"] == vat
            assert body["final_amount"] == final


# --------------------------------------------------------------------------- #
# TC-0015 success state path 3200 -> 3400 -> 30 (credit) with debit applied
# --------------------------------------------------------------------------- #
def test_tc0015_success_transitions_to_credit_and_debits():
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_validate(request_payload(amount="100.00"))
        assert status == 200
        assert body["state"] == int(State.CREDIT)
        assert sys.account_repository.find_by_account_id("ACC1").balance == Decimal("884.00")


# --------------------------------------------------------------------------- #
# TC-0016 audit state on blacklist OR insufficient funds
# --------------------------------------------------------------------------- #
def test_tc0016_audit_state_on_failures():
    with running_system([account(status="B", balance="1000.00")]) as sys:
        _, blk = sys.post_validate(request_payload(txn_id="B1", amount="100.00"))
    with running_system([account(balance="10.00")]) as sys:
        _, insuf = sys.post_validate(request_payload(txn_id="I1", amount="100.00"))
    assert blk["state"] == int(State.AUDIT)
    assert insuf["state"] == int(State.AUDIT)


# --------------------------------------------------------------------------- #
# TC-0017 database retrieval + balance update atomicity under concurrency
# --------------------------------------------------------------------------- #
def test_tc0017_concurrent_debits_no_lost_update():
    # 30 concurrent HTTP requests to the same account; final 116.00 each.
    # Balance only covers some of them; successes must debit exactly, with no
    # lost updates and no negative balance.
    start = Decimal("1000.00")
    with running_system([account(balance=str(start))]) as sys:
        results: list[tuple[int, dict]] = []
        lock = threading.Lock()

        def fire(i: int) -> None:
            r = sys.post_validate(request_payload(txn_id=f"C{i}", amount="100.00"))
            with lock:
                results.append(r)

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [b for (st, b) in results if st == 200]
        final_balance = sys.account_repository.find_by_account_id("ACC1").balance
        # Exactly start - 116 * successes, never negative.
        assert final_balance == start - Decimal("116.00") * len(successes)
        assert final_balance >= Decimal("0.00")
        assert len(results) == 30


# --------------------------------------------------------------------------- #
# TC-0018 transaction log engine receives complete details
# --------------------------------------------------------------------------- #
def test_tc0018_transaction_log_completeness():
    with running_system([account(balance="1000.00")]) as sys:
        sys.post_validate(request_payload(txn_id="LOG1", amount="100.00", trans_type=2))
        logged = sys.log_engine.get("LOG1")
        assert logged is not None
        assert logged.success is True
        assert logged.state == State.CREDIT
        assert logged.error_code == ErrorCode.OK
        assert logged.original_amount == Decimal("100.00")
        assert logged.fee == Decimal("12.50")
        assert logged.vat == Decimal("18.00")
        assert logged.final_amount == Decimal("130.50")
        assert logged.account_id == "ACC1"
        # failures are logged too, with the audit state
        sys.post_validate(request_payload(txn_id="LOG2", amount="100.00", account_id="NOPE"))
        miss = sys.log_engine.get("LOG2")
        assert miss is not None and miss.success is False


# --------------------------------------------------------------------------- #
# Cross-service: BR-0009 payroll parity resolved via customer lookup
# --------------------------------------------------------------------------- #
def test_br0009_even_employee_blocked_odd_allowed():
    from payment_message_processing import Customer

    customers = [Customer(customer_id="EVEN", employee_id=44), Customer("ODD", 7)]
    with running_system([account(balance="1000.00")], customers=customers) as sys:
        _, even = sys.post_validate(request_payload(txn_id="E", customer_id="EVEN"))
        _, odd = sys.post_validate(request_payload(txn_id="O", customer_id="ODD"))
    assert even["error_code"] == "WS-ERR-AUTH-DENIED"
    assert odd["success"] is True


def test_br0009_unknown_customer_skips_check():
    with running_system([account(balance="1000.00")]) as sys:
        _, body = sys.post_validate(request_payload(customer_id="GHOST"))
    assert body["success"] is True


# --------------------------------------------------------------------------- #
# Idempotency across two separate HTTP calls (no double debit)
# --------------------------------------------------------------------------- #
def test_idempotent_replay_no_double_debit():
    with running_system([account(balance="1000.00")]) as sys:
        s1, b1 = sys.post_validate(request_payload(txn_id="DUP", amount="100.00"))
        s2, b2 = sys.post_validate(request_payload(txn_id="DUP", amount="100.00"))
        assert s1 == 200 and s2 == 200
        assert b1["duplicate"] is False
        assert b2["duplicate"] is True
        # debited exactly once
        assert sys.account_repository.find_by_account_id("ACC1").balance == Decimal("884.00")


# --------------------------------------------------------------------------- #
# Error-code -> HTTP status mapping consistency across the boundary
# --------------------------------------------------------------------------- #
def test_error_code_http_status_mapping():
    # account not found -> 404
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_validate(request_payload(account_id="MISSING"))
        assert status == 404
        assert body["error_code"] == "WS-ERR-ACCOUNT-NOT-FOUND"


def test_malformed_request_is_400():
    with running_system() as sys:
        # missing transaction_id
        status, body = sys.post_validate({"message": {4: "10000", 3: 0, 102: "ACC1"}})
        assert status == 400
        assert body["error_code"] == "WS-ERR-INVALID-FORMAT"
