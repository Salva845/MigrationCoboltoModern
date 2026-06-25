"""Acceptance + unit tests for payment-message-processing (TC-0001..TC-0018).

The service is a thin HTTP wrapper over payment-processing-core, so these tests
exercise the controller end-to-end (request parsing -> rules -> debit -> response
-> HTTP status) and assert the prompt-02-specific behaviours: fraud blocks on
score > 60 (TC-0006 allows exactly 60), BR-0009 payroll parity, idempotency
flags, error-code -> HTTP status mapping, and the edge cases from section 8.
"""

import json
import threading
import urllib.request
from decimal import Decimal

import pytest
from payment_processing_core import (
    Account,
    ErrorCode,
    FraudScoringEngine,
    InMemoryAccountRepository,
    InMemoryTransactionLogEngine,
    State,
)

from payment_message_processing import (
    Customer,
    InMemoryCustomerRepository,
    TransactionValidationController,
    build_server,
    parse_request,
    status_for,
)

# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #
MIN_AMOUNT = Decimal("1.00")
MAX_AMOUNT = Decimal("1000000.00")


def acc(account_id="ACC1", status="A", balance="100000.00", risk=0, acc_type="CHECKING", version=0):
    return Account(
        account_id=account_id,
        status=status,
        balance=Decimal(balance),
        risk_level=risk,
        account_type=acc_type,
        version=version,
    )


class StubFraud(FraudScoringEngine):
    """Deterministic score so boundary tests do not depend on heuristics."""

    def __init__(self, value: int):
        self.value = value

    def score(self, amount, risk_level):
        return self.value


def build_controller(accounts=None, customers=None, fraud=None, log_engine=None):
    return TransactionValidationController(
        account_repository=InMemoryAccountRepository(accounts or [acc()]),
        customer_repository=InMemoryCustomerRepository(customers or []),
        log_engine=log_engine or InMemoryTransactionLogEngine(),
        fraud_engine=fraud,
    )


def minor(amount: str) -> str:
    """Major units -> ISO 8583 DE004 minor units (cents) as a digit string."""
    return str(int((Decimal(amount) * 100).to_integral_value()))


def msg(amount="100.00", trans_type=0, account_id="ACC1", **extra):
    body = {4: minor(amount), 3: trans_type, 102: account_id}
    body.update(extra)
    return body


def request(txn_id="T1", amount="100.00", trans_type=0, account_id="ACC1", customer_id=None):
    payload = {"transaction_id": txn_id, "message": msg(amount, trans_type, account_id)}
    if customer_id is not None:
        payload["customer_id"] = customer_id
    return payload


# --------------------------------------------------------------------------- #
# TC-0001 happy path
# --------------------------------------------------------------------------- #
def test_tc0001_happy_path_end_to_end():
    ctrl = build_controller([acc(balance="1000.00")])
    resp = ctrl.validate(request(amount="100.00", trans_type=0))
    assert resp.status_code == 200
    b = resp.body
    assert b["success"] is True
    assert b["status"] == "WS-OK"
    assert b["state"] == int(State.CREDIT) == 30
    # fee 0, vat = 100 * 0.16 = 16.00, final = 116.00
    assert b["fee"] == "0.00"
    assert b["vat"] == "16.00"
    assert b["final_amount"] == "116.00"
    # account actually debited
    acct = ctrl.validator.repository.find_by_account_id("ACC1")
    assert acct.balance == Decimal("884.00")


# --------------------------------------------------------------------------- #
# TC-0002..TC-0005 amount thresholds (strict < and >)
# --------------------------------------------------------------------------- #
def test_tc0002_amount_at_minimum_passes():
    ctrl = build_controller()
    resp = ctrl.validate(request(amount="1.00"))
    assert resp.status_code == 200
    assert resp.body["success"] is True


def test_tc0003_amount_below_minimum_rejected():
    ctrl = build_controller()
    resp = ctrl.validate(request(amount="0.99"))
    assert resp.status_code == 400
    assert resp.body["error_code"] == "WS-ERR-INVALID-FORMAT"
    assert resp.body["success"] is False


def test_tc0004_amount_at_maximum_passes():
    ctrl = build_controller([acc(balance="2000000.00")])
    resp = ctrl.validate(request(amount="1000000.00"))
    assert resp.status_code == 200
    assert resp.body["success"] is True


def test_tc0005_amount_above_maximum_rejected():
    ctrl = build_controller([acc(balance="2000000.00")])
    resp = ctrl.validate(request(amount="1000000.01"))
    assert resp.status_code == 400
    assert resp.body["error_code"] == "WS-ERR-INVALID-FORMAT"


# --------------------------------------------------------------------------- #
# TC-0006 / TC-0007 fraud boundary (prompt 02: > 60 blocks, 60 allows)
# --------------------------------------------------------------------------- #
def test_tc0006_fraud_score_60_allows():
    ctrl = build_controller(fraud=StubFraud(60))
    resp = ctrl.validate(request())
    assert resp.status_code == 200
    assert resp.body["success"] is True


def test_tc0006b_fraud_score_61_blocks():
    ctrl = build_controller(fraud=StubFraud(61))
    resp = ctrl.validate(request())
    assert resp.status_code == 403
    assert resp.body["error_code"] == "WS-ERR-BLACKLISTED"
    assert "fraud" in resp.body["message"].lower()


def test_tc0007_fraud_score_59_allows():
    ctrl = build_controller(fraud=StubFraud(59))
    resp = ctrl.validate(request())
    assert resp.status_code == 200
    assert resp.body["success"] is True


# --------------------------------------------------------------------------- #
# TC-0008 SPEI fee
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "trans_type,expected_fee", [(0, "0.00"), (1, "0.00"), (2, "12.50"), (3, "0.00")]
)
def test_tc0008_spei_fee_only_for_type_2(trans_type, expected_fee):
    # type 2 from a non-NOMINA account so BR-0004 does not pre-empt the fee.
    ctrl = build_controller([acc(acc_type="CHECKING", balance="100000.00")])
    resp = ctrl.validate(request(amount="100.00", trans_type=trans_type))
    assert resp.body["fee"] == expected_fee


# --------------------------------------------------------------------------- #
# TC-0009 / TC-0010 insufficient funds boundary
# --------------------------------------------------------------------------- #
def test_tc0009_balance_exactly_sufficient_allows():
    # final = 116.00 for amount 100.00, no fee.
    ctrl = build_controller([acc(balance="116.00")])
    resp = ctrl.validate(request(amount="100.00"))
    assert resp.status_code == 200
    assert resp.body["success"] is True
    assert ctrl.validator.repository.find_by_account_id("ACC1").balance == Decimal("0.00")


def test_tc0010_balance_one_unit_below_triggers_insufficient():
    ctrl = build_controller([acc(balance="115.99")])
    resp = ctrl.validate(request(amount="100.00"))
    assert resp.status_code == 409
    assert resp.body["error_code"] == "WS-ERR-INSUFF-FUNDS"
    assert resp.body["state"] == int(State.AUDIT) == 40
    # not debited
    assert ctrl.validator.repository.find_by_account_id("ACC1").balance == Decimal("115.99")


# --------------------------------------------------------------------------- #
# TC-0011 blacklist
# --------------------------------------------------------------------------- #
def test_tc0011_blacklisted_account_rejected_and_audited():
    ctrl = build_controller([acc(status="B")])
    resp = ctrl.validate(request())
    assert resp.status_code == 403
    assert resp.body["error_code"] == "WS-ERR-BLACKLISTED"
    assert resp.body["state"] == 40


# --------------------------------------------------------------------------- #
# TC-0012 / TC-0013 account type restriction
# --------------------------------------------------------------------------- #
def test_tc0012_spei_from_nomina_rejected():
    ctrl = build_controller([acc(acc_type="NOMINA")])
    resp = ctrl.validate(request(trans_type=2))
    assert resp.status_code == 403
    assert resp.body["error_code"] == "WS-ERR-AUTH-DENIED"


def test_tc0013_non_spei_from_nomina_allowed():
    ctrl = build_controller([acc(acc_type="NOMINA", balance="1000.00")])
    resp = ctrl.validate(request(trans_type=1))
    assert resp.status_code == 200
    assert resp.body["success"] is True


# --------------------------------------------------------------------------- #
# TC-0014 VAT / total formula equivalence
# --------------------------------------------------------------------------- #
def test_tc0014_vat_formula_with_fee():
    ctrl = build_controller([acc(balance="100000.00")])
    resp = ctrl.validate(request(amount="100.00", trans_type=2, account_id="ACC1"))
    # SPEI: fee 12.50; vat = (100 + 12.50) * 0.16 = 18.00; final = 100 + 12.50 + 18.00
    assert resp.body["fee"] == "12.50"
    assert resp.body["vat"] == "18.00"
    assert resp.body["final_amount"] == "130.50"


def test_tc0014b_vat_half_up_rounding():
    # 100.01 + fee 0 -> vat = 16.0016 -> HALF_UP -> 16.00
    ctrl = build_controller([acc(balance="100000.00")])
    resp = ctrl.validate(request(amount="100.01"))
    assert resp.body["vat"] == "16.00"
    assert resp.body["final_amount"] == "116.01"


# --------------------------------------------------------------------------- #
# TC-0015 / TC-0016 state transitions
# --------------------------------------------------------------------------- #
def test_tc0015_success_state_is_credit():
    ctrl = build_controller([acc(balance="1000.00")])
    resp = ctrl.validate(request())
    assert resp.body["state"] == 30


def test_tc0016_audit_state_on_rejection():
    ctrl = build_controller([acc(balance="1.00")])
    resp = ctrl.validate(request(amount="100.00"))
    assert resp.body["state"] == 40


# --------------------------------------------------------------------------- #
# TC-0017 atomicity under concurrency
# --------------------------------------------------------------------------- #
def test_tc0017_concurrent_debits_no_lost_update():
    repo = InMemoryAccountRepository([acc(balance="100000.00")])
    log = InMemoryTransactionLogEngine()
    ctrl = TransactionValidationController(
        account_repository=repo,
        customer_repository=InMemoryCustomerRepository(),
        log_engine=log,
    )
    results = []
    n = 20

    def worker(i):
        results.append(ctrl.validate(request(txn_id=f"T{i}", amount="100.00")))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.body["success"]]
    final = repo.find_by_account_id("ACC1").balance
    # balance never negative and exactly reflects the number of successful debits
    assert final >= Decimal("0.00")
    assert final == Decimal("100000.00") - Decimal("116.00") * len(successes)


# --------------------------------------------------------------------------- #
# TC-0018 transaction log completeness
# --------------------------------------------------------------------------- #
def test_tc0018_log_engine_receives_full_details():
    log = InMemoryTransactionLogEngine()
    ctrl = build_controller([acc(balance="1000.00")], log_engine=log)
    ctrl.validate(request(txn_id="LOG1"))
    entry = log.get("LOG1")
    assert entry is not None
    assert entry.transaction_id == "LOG1"
    assert entry.account_id == "ACC1"
    assert entry.final_amount == Decimal("116.00")
    assert entry.state == State.CREDIT
    assert entry.error_code == ErrorCode.OK


# --------------------------------------------------------------------------- #
# BR-0009 payroll employee parity
# --------------------------------------------------------------------------- #
def test_br0009_even_employee_blocks_payroll():
    ctrl = build_controller([acc(balance="1000.00")], customers=[Customer("CUST1", employee_id=42)])
    resp = ctrl.validate(request(customer_id="CUST1"))
    assert resp.status_code == 403
    assert resp.body["error_code"] == "WS-ERR-AUTH-DENIED"
    assert "payroll" in resp.body["message"].lower()
    # blocked before debit
    assert ctrl.validator.repository.find_by_account_id("ACC1").balance == Decimal("1000.00")


def test_br0009_odd_employee_allows_payroll():
    ctrl = build_controller([acc(balance="1000.00")], customers=[Customer("CUST1", employee_id=43)])
    resp = ctrl.validate(request(customer_id="CUST1"))
    assert resp.status_code == 200
    assert resp.body["success"] is True


def test_br0009_zero_employee_id_blocks():
    ctrl = build_controller([acc(balance="1000.00")], customers=[Customer("CUST0", employee_id=0)])
    resp = ctrl.validate(request(customer_id="CUST0"))
    assert resp.status_code == 403
    assert resp.body["error_code"] == "WS-ERR-AUTH-DENIED"


def test_br0009_missing_customer_skips_validation():
    ctrl = build_controller([acc(balance="1000.00")])
    resp = ctrl.validate(request(customer_id="DOES-NOT-EXIST"))
    assert resp.status_code == 200
    assert resp.body["success"] is True


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def test_idempotent_replay_flags_duplicate_and_no_double_debit():
    ctrl = build_controller([acc(balance="1000.00")])
    first = ctrl.validate(request(txn_id="DUP"))
    assert first.body["duplicate"] is False
    second = ctrl.validate(request(txn_id="DUP"))
    assert second.body["duplicate"] is True
    assert second.body["final_amount"] == first.body["final_amount"]
    # only one debit applied
    assert ctrl.validator.repository.find_by_account_id("ACC1").balance == Decimal("884.00")


# --------------------------------------------------------------------------- #
# Request validation / edge cases (section 8)
# --------------------------------------------------------------------------- #
def test_missing_transaction_id_is_400():
    ctrl = build_controller()
    resp = ctrl.validate({"message": msg()})
    assert resp.status_code == 400
    assert resp.body["error_code"] == "WS-ERR-INVALID-FORMAT"


def test_malformed_iso_message_is_400():
    ctrl = build_controller()
    resp = ctrl.validate({"transaction_id": "X", "message": {4: "abc", 3: 0, 102: "ACC1"}})
    assert resp.status_code == 400
    assert resp.body["error_code"] == "WS-ERR-INVALID-FORMAT"


def test_account_not_found_is_404():
    ctrl = build_controller([acc(account_id="OTHER")])
    resp = ctrl.validate(request(account_id="ACC1"))
    assert resp.status_code == 404
    assert resp.body["status"] == "WS-ERR-ACCOUNT-NOT-FOUND"


def test_zero_amount_rejected():
    ctrl = build_controller()
    resp = ctrl.validate(request(amount="0.00"))
    assert resp.status_code == 400
    assert resp.body["error_code"] == "WS-ERR-INVALID-FORMAT"


def test_non_dict_payload_rejected():
    ctrl = build_controller()
    resp = ctrl.validate(["not", "an", "object"])
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Parser / DTO units
# --------------------------------------------------------------------------- #
def test_parse_request_extracts_customer_from_iso_field_103():
    parsed = parse_request(
        {"transaction_id": "T", "message": {4: "10000", 3: 0, 102: "A", 103: "C9"}}
    )
    assert parsed.customer_id == "C9"
    assert parsed.transaction_id == "T"


def test_status_for_maps_all_documented_codes():
    assert status_for(ErrorCode.INVALID_FORMAT) == 400
    assert status_for(ErrorCode.BLACKLISTED) == 403
    assert status_for(ErrorCode.AUTH_DENIED) == 403
    assert status_for(ErrorCode.INSUFF_FUNDS) == 409
    assert status_for(ErrorCode.OK) == 200


def test_to_response_serialises_decimals_as_strings():
    ctrl = build_controller([acc(balance="1000.00")])
    resp = ctrl.validate(request(txn_id="SER"))
    body = resp.body
    # all monetary fields are strings (no float precision loss over the wire)
    for key in ("original_amount", "fee", "vat", "final_amount"):
        assert isinstance(body[key], str)
    json.dumps(body)  # must be JSON-serialisable


# --------------------------------------------------------------------------- #
# HTTP transport (end-to-end over a real socket)
# --------------------------------------------------------------------------- #
def test_http_post_validate_end_to_end():
    ctrl = build_controller([acc(balance="1000.00")])
    server = build_server(ctrl, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps(request(txn_id="HTTP1")).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/transactions/validate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:  # noqa: S310 - localhost test server.
            assert r.status == 200
            body = json.loads(r.read().decode("utf-8"))
        assert body["success"] is True
        assert body["final_amount"] == "116.00"
    finally:
        server.shutdown()
        server.server_close()


def test_http_unknown_path_is_404():
    ctrl = build_controller()
    server = build_server(ctrl, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/nope", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)  # noqa: S310 - localhost test server.
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
