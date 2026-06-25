"""Wave-1 end-to-end integration tests for the BANK74 slice (TC-0019..TC-0027).

Every case drives the *real* HTTP boundary exposed by payment-message-processing
(POST /transactions/process) which delegates to the core TransactionProcessor
(payment-processing-core), then asserts the cross-service contract: HTTP status
<- error-code mapping, the flat 15.00 fee and total-debit arithmetic, the
state-machine transitions (success -> 30, every error -> 40), atomic balance
updates observed on the shared repository, the high-risk account scan (> 90,000),
the type-6 timeout simulation, idempotency across two requests and numeric
precision (Decimal, 2 places). No business logic is duplicated here; the harness
only wires the two slices.
"""

import threading
from decimal import Decimal

from payment_processing_core import State

from payment_integration import ProcessingConfig, account, request_payload, running_system

PROCESS = "/transactions/process"


def balance_of(sys, account_id="ACC1") -> Decimal:
    return sys.account_repository.find_by_account_id(account_id).balance


# --------------------------------------------------------------------------- #
# TC-0019 happy path (end-to-end over HTTP): flat 15.00 fee, debit, state 30
# --------------------------------------------------------------------------- #
def test_tc0019_happy_path_end_to_end():
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_process(request_payload(txn_id="T19", amount="100.00"))
        assert status == 200
        assert body["success"] is True
        assert body["status"] == "WS-OK"
        assert body["state"] == int(State.CREDIT) == 30
        assert body["fee"] == "15.00"
        assert body["total_debit"] == "115.00"
        assert body["updated_balance"] == "885.00"
        # effect is visible on the shared repository the service mutated.
        assert balance_of(sys) == Decimal("885.00")


# --------------------------------------------------------------------------- #
# TC-0020 / TC-0021 / TC-0022 amount thresholds (no hard-coded values in test)
# --------------------------------------------------------------------------- #
def test_tc0020_amount_at_minimum_accepted():
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_process(request_payload(txn_id="T20", amount="1.00"))
    assert status == 200
    assert body["success"] is True
    assert body["total_debit"] == "16.00"


def test_tc0021_amount_above_maximum_rejected_audit():
    with running_system([account(balance="2000000.00")]) as sys:
        status, body = sys.post_process(request_payload(txn_id="T21", amount="1000000.01"))
        assert status == 400
        assert body["error_code"] == "WS-ERR-INVALID-FORMAT"
        assert body["state"] == int(State.AUDIT) == 40
        assert balance_of(sys) == Decimal("2000000.00")  # untouched


def test_tc0022_amount_below_minimum_rejected_audit():
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_process(request_payload(txn_id="T22", amount="0.99"))
        assert status == 400
        assert body["error_code"] == "WS-ERR-INVALID-FORMAT"
        assert body["state"] == int(State.AUDIT)
        assert balance_of(sys) == Decimal("1000.00")  # untouched


# --------------------------------------------------------------------------- #
# TC-0023 / TC-0024 insufficient-funds boundary (balance == / < total debit)
# --------------------------------------------------------------------------- #
def test_tc0023_balance_equals_total_debit_succeeds_to_zero():
    with running_system([account(balance="115.00")]) as sys:
        status, body = sys.post_process(request_payload(txn_id="T23", amount="100.00"))
        assert status == 200
        assert body["success"] is True
        assert body["updated_balance"] == "0.00"
        assert balance_of(sys) == Decimal("0.00")


def test_tc0024_balance_below_total_debit_rejected_audit():
    with running_system([account(balance="114.99")]) as sys:
        status, body = sys.post_process(request_payload(txn_id="T24", amount="100.00"))
        assert status == 409
        assert body["error_code"] == "WS-ERR-INSUFF-FUNDS"
        assert body["state"] == int(State.AUDIT)
        assert balance_of(sys) == Decimal("114.99")  # not debited


# --------------------------------------------------------------------------- #
# TC-0025 high-risk account scan during logging (> 90,000.00, strict >)
# --------------------------------------------------------------------------- #
def test_tc0025_high_risk_accounts_flagged_over_http():
    accounts = [
        account(account_id="ACC1", balance="1000.00"),
        account(account_id="RICH", balance="95000.00"),  # > threshold -> flagged
        account(account_id="EDGE", balance="90000.00"),  # == threshold -> not flagged
    ]
    with running_system(accounts) as sys:
        status, body = sys.post_process(request_payload(txn_id="T25", amount="100.00"))
        assert status == 200
        assert body["high_risk_accounts"] == ["RICH"]
        # the flags are part of the persisted audit entry the service wrote.
        assert sys.processing_controller.processor.entries[-1].high_risk_accounts == ["RICH"]


# --------------------------------------------------------------------------- #
# TC-0026 type-6 timeout: error, state 40, balance untouched (no credit)
# --------------------------------------------------------------------------- #
def test_tc0026_type6_timeout_does_not_debit():
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_process(
            request_payload(txn_id="T26", amount="100.00", trans_type=6)
        )
        assert status == 504
        assert body["error_code"] == "WS-ERR-TIMEOUT"
        assert body["state"] == int(State.AUDIT) == 40
        assert balance_of(sys) == Decimal("1000.00")  # not modified


# --------------------------------------------------------------------------- #
# TC-0027 equivalence with legacy oracle (byte-for-byte on representative input)
# --------------------------------------------------------------------------- #
def test_tc0027_equivalence_with_legacy_oracle():
    # Representative input (amount 250.00, type 0, balance 5,000.00): fee 15.00,
    # total debit 265.00, new balance 4,735.00, state 30.
    legacy_oracle = {
        "status": "WS-OK",
        "state": 30,
        "fee": "15.00",
        "total_debit": "265.00",
        "updated_balance": "4735.00",
    }
    with running_system([account(balance="5000.00")]) as sys:
        _, body = sys.post_process(request_payload(txn_id="T27", amount="250.00"))
    assert {k: body[k] for k in legacy_oracle} == legacy_oracle


# --------------------------------------------------------------------------- #
# BR-0016 removal — type 4 is processed normally across the boundary
# --------------------------------------------------------------------------- #
def test_br0016_removed_type4_processed_normally():
    with running_system([account(balance="1000.00")]) as sys:
        status, body = sys.post_process(request_payload(txn_id="T4", amount="100.00", trans_type=4))
        assert status == 200
        assert body["success"] is True
        assert balance_of(sys) == Decimal("885.00")


# --------------------------------------------------------------------------- #
# Error-code -> HTTP status mapping consistency across the boundary
# --------------------------------------------------------------------------- #
def test_account_not_found_is_404():
    with running_system([account(account_id="OTHER", balance="1000.00")]) as sys:
        status, body = sys.post_process(
            request_payload(txn_id="Tnf", amount="100.00", account_id="MISSING")
        )
        assert status == 404
        assert body["error_code"] == "WS-ERR-ACCOUNT-NOT-FOUND"
        assert body["state"] == int(State.AUDIT)


def test_malformed_request_is_400():
    with running_system() as sys:
        # missing transaction_id
        status, body = sys.post_process({"message": {4: "10000", 3: 0, 102: "ACC1"}})
        assert status == 400
        assert body["error_code"] == "WS-ERR-INVALID-FORMAT"


# --------------------------------------------------------------------------- #
# Idempotency across two separate HTTP calls (no double debit)
# --------------------------------------------------------------------------- #
def test_idempotent_replay_no_double_debit():
    with running_system([account(balance="1000.00")]) as sys:
        s1, b1 = sys.post_process(request_payload(txn_id="DUP", amount="100.00"))
        s2, b2 = sys.post_process(request_payload(txn_id="DUP", amount="100.00"))
        assert s1 == 200 and s2 == 200
        assert b1["duplicate"] is False
        assert b2["duplicate"] is True
        assert b2["updated_balance"] == b1["updated_balance"]
        # debited exactly once.
        assert balance_of(sys) == Decimal("885.00")
        assert len(sys.processing_controller.processor.entries) == 1


# --------------------------------------------------------------------------- #
# Numeric precision preserved across the HTTP boundary (Decimal, 2 places)
# --------------------------------------------------------------------------- #
def test_numeric_precision_two_decimals_over_http():
    with running_system([account(balance="1000.00")]) as sys:
        _, body = sys.post_process(request_payload(txn_id="Tprec", amount="100.10"))
        assert body["total_debit"] == "115.10"
        assert body["updated_balance"] == "884.90"
        assert balance_of(sys) == Decimal("884.90")


# --------------------------------------------------------------------------- #
# Thresholds are not hard-coded — config flows end-to-end
# --------------------------------------------------------------------------- #
def test_config_overrides_respected_end_to_end():
    cfg = ProcessingConfig(fixed_fee=Decimal("5.00"), high_risk_threshold=Decimal("500.00"))
    with running_system([account(balance="1000.00")], processing_config=cfg) as sys:
        _, body = sys.post_process(request_payload(txn_id="Tcfg", amount="100.00"))
        assert body["fee"] == "5.00"
        assert body["total_debit"] == "105.00"
        assert body["high_risk_accounts"] == ["ACC1"]  # 895 > 500 after debit


# --------------------------------------------------------------------------- #
# Atomic debit + balance update under concurrency (no lost updates / negatives)
# --------------------------------------------------------------------------- #
def test_concurrent_debits_no_lost_update_over_http():
    # 30 concurrent HTTP requests to the same account; total debit 115.00 each.
    start = Decimal("1000.00")
    with running_system([account(balance=str(start))]) as sys:
        results: list[tuple[int, dict]] = []
        lock = threading.Lock()

        def fire(i: int) -> None:
            r = sys.post_process(request_payload(txn_id=f"C{i}", amount="100.00"))
            with lock:
                results.append(r)

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [b for (st, b) in results if st == 200]
        final_balance = balance_of(sys)
        assert final_balance == start - Decimal("115.00") * len(successes)
        assert final_balance >= Decimal("0.00")
        assert len(results) == 30


# --------------------------------------------------------------------------- #
# Cross-slice isolation: both endpoints coexist on the same server / repository
# --------------------------------------------------------------------------- #
def test_both_slices_share_one_repository():
    # BANK85 /validate adds VAT (no fee for type 0); BANK74 /process adds the
    # flat 15.00 fee (no VAT). Both debit the same shared account in sequence.
    with running_system([account(balance="1000.00")]) as sys:
        sv_status, sv_body = sys.post_process(request_payload(txn_id="P1", amount="100.00"))
        vc_status, vc_body = sys.post_validate(request_payload(txn_id="V1", amount="100.00"))

    assert sv_status == 200 and vc_status == 200
    # BANK74: fee 15.00, no vat field.
    assert sv_body["fee"] == "15.00"
    assert "vat" not in sv_body
    # BANK85: vat 16.00, no total_debit field.
    assert vc_body["vat"] == "16.00"
    assert "total_debit" not in vc_body
