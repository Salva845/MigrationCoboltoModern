"""Acceptance tests TC-0001..TC-0018 plus business-rule and edge-case coverage.

Threshold constants come from RuleConfig defaults:
  min=1.00  max=1000000.00  vat=0.16  spei_fee=12.50  fraud_block>=60  high_balance>80000
"""

from decimal import Decimal

import pytest

from payment_processing_core import (
    Account,
    ErrorCode,
    FraudScoringEngine,
    InMemoryAccountRepository,
    InMemoryTransactionLogEngine,
    Iso8583Message,
    State,
    TransactionValidator,
)
from payment_processing_core.repository import AccountRepository, ConcurrencyError


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_account(
    account_id="ACC1",
    status="A",
    balance="1000.00",
    risk_level=1,
    account_type="CHECKING",
    version=0,
):
    return Account(account_id, status, Decimal(balance), risk_level, account_type, version)


def make_validator(accounts, fraud_engine=None, config=None, repo=None):
    repo = repo or InMemoryAccountRepository(accounts)
    log = InMemoryTransactionLogEngine()
    validator = TransactionValidator(repo, log, fraud_engine=fraud_engine, config=config)
    return validator, repo, log


def msg(account_id="ACC1", amount="100.00", trans_type=2):
    return Iso8583Message(amount=Decimal(amount), trans_type=trans_type, account_id=account_id)


class StubFraud(FraudScoringEngine):
    def __init__(self, fixed_score):
        self.fixed_score = fixed_score

    def score(self, amount, risk_level):
        return self.fixed_score


# --------------------------------------------------------------------------- #
# TC-0001 Happy path
# --------------------------------------------------------------------------- #
def test_tc0001_happy_path():
    validator, repo, log = make_validator([make_account(balance="1000.00")])
    res = validator.process("tx1", msg(amount="100.00", trans_type=2))

    assert res.success is True
    assert res.error_code is ErrorCode.OK
    assert res.state is State.CREDIT  # 30
    assert res.fee == Decimal("12.50")
    assert res.vat == Decimal("18.00")  # (100 + 12.50) * 0.16
    assert res.final_amount == Decimal("130.50")
    assert repo.find_by_account_id("ACC1").balance == Decimal("869.50")
    assert log.exists("tx1")


# --------------------------------------------------------------------------- #
# TC-0002 / TC-0003 minimum threshold (inclusive)
# --------------------------------------------------------------------------- #
def test_tc0002_amount_at_minimum_passes():
    validator, _, _ = make_validator([make_account(balance="1000.00")])
    res = validator.process("tx", msg(amount="1.00", trans_type=1))
    assert res.success is True


def test_tc0003_amount_below_minimum_rejected():
    validator, repo, _ = make_validator([make_account()])
    res = validator.process("tx", msg(amount="0.99", trans_type=1))
    assert res.success is False
    assert res.error_code is ErrorCode.INVALID_FORMAT
    assert repo.find_by_account_id("ACC1").balance == Decimal("1000.00")  # not debited


# --------------------------------------------------------------------------- #
# TC-0004 / TC-0005 maximum threshold (inclusive)
# --------------------------------------------------------------------------- #
def test_tc0004_amount_at_maximum_passes():
    validator, _, _ = make_validator([make_account(balance="2000000.00")])
    res = validator.process("tx", msg(amount="1000000.00", trans_type=1))
    assert res.success is True


def test_tc0005_amount_above_maximum_rejected():
    validator, _, _ = make_validator([make_account(balance="2000000.00")])
    res = validator.process("tx", msg(amount="1000000.01", trans_type=1))
    assert res.success is False
    assert res.error_code is ErrorCode.INVALID_FORMAT


# --------------------------------------------------------------------------- #
# TC-0006 / TC-0007 fraud score boundary (>= 60 blocks)
# --------------------------------------------------------------------------- #
def test_tc0006_fraud_score_60_blocks():
    validator, repo, _ = make_validator([make_account()], fraud_engine=StubFraud(60))
    res = validator.process("tx", msg(amount="100.00", trans_type=1))
    assert res.success is False
    assert res.error_code is ErrorCode.BLACKLISTED
    assert "fraud score" in res.message
    assert repo.find_by_account_id("ACC1").balance == Decimal("1000.00")  # not debited


def test_tc0007_fraud_score_59_allows():
    validator, _, _ = make_validator([make_account()], fraud_engine=StubFraud(59))
    res = validator.process("tx", msg(amount="100.00", trans_type=1))
    assert res.success is True


def test_default_fraud_scoring_points():
    validator, _, _ = make_validator([make_account()])
    # >50000 amount (+40) and risk>2 (+30) -> 70 >= 60 blocks
    acc = make_account(balance="1000000.00", risk_level=3)
    validator, repo, _ = make_validator([acc])
    res = validator.process("tx", msg(amount="60000.00", trans_type=1))
    assert res.error_code is ErrorCode.BLACKLISTED


# --------------------------------------------------------------------------- #
# TC-0008 SPEI fee
# --------------------------------------------------------------------------- #
def test_tc0008_spei_fee():
    validator, _, _ = make_validator([make_account()])
    assert validator.br0006_spei_fee(2) == Decimal("12.50")
    assert validator.br0006_spei_fee(1) == Decimal("0.00")


# --------------------------------------------------------------------------- #
# TC-0009 / TC-0010 balance sufficiency boundary
# --------------------------------------------------------------------------- #
def test_tc0009_balance_exactly_sufficient_debits_to_zero():
    validator, repo, _ = make_validator([make_account(balance="130.50")])
    res = validator.process("tx", msg(amount="100.00", trans_type=2))
    assert res.success is True
    assert repo.find_by_account_id("ACC1").balance == Decimal("0.00")


def test_tc0010_balance_one_unit_below_rejected_not_debited():
    validator, repo, _ = make_validator([make_account(balance="130.49")])
    res = validator.process("tx", msg(amount="100.00", trans_type=2))
    assert res.success is False
    assert res.error_code is ErrorCode.INSUFF_FUNDS
    assert res.state is State.AUDIT  # 40
    assert repo.find_by_account_id("ACC1").balance == Decimal("130.49")  # untouched


# --------------------------------------------------------------------------- #
# TC-0011 blacklist
# --------------------------------------------------------------------------- #
def test_tc0011_blacklist_rejected_audit_state():
    validator, repo, _ = make_validator([make_account(status="B")])
    res = validator.process("tx", msg(amount="100.00", trans_type=2))
    assert res.success is False
    assert res.error_code is ErrorCode.BLACKLISTED
    assert res.state is State.AUDIT
    assert "blacklist" in res.message
    assert repo.find_by_account_id("ACC1").balance == Decimal("1000.00")


# --------------------------------------------------------------------------- #
# TC-0012 / TC-0013 account-type restriction
# --------------------------------------------------------------------------- #
def test_tc0012_spei_from_nomina_rejected():
    validator, _, _ = make_validator([make_account(account_type="NOMINA")])
    res = validator.process("tx", msg(amount="100.00", trans_type=2))
    assert res.success is False
    assert res.error_code is ErrorCode.AUTH_DENIED


def test_tc0013_non_spei_from_nomina_allowed():
    validator, _, _ = make_validator([make_account(account_type="NOMINA")])
    res = validator.process("tx", msg(amount="100.00", trans_type=1))
    assert res.success is True


# --------------------------------------------------------------------------- #
# TC-0014 VAT / total formula
# --------------------------------------------------------------------------- #
def test_tc0014_vat_and_total_formula():
    validator, _, _ = make_validator([make_account()])
    vat, final = validator.br0007_vat_and_total(Decimal("100.00"), Decimal("12.50"))
    assert vat == Decimal("18.00")  # (100 + 12.50) * 0.16
    assert final == Decimal("130.50")  # 100 + 12.50 + 18.00
    # no compounding: VAT not applied to VAT
    vat2, final2 = validator.br0007_vat_and_total(Decimal("200.00"), Decimal("0.00"))
    assert vat2 == Decimal("32.00")
    assert final2 == Decimal("232.00")


# --------------------------------------------------------------------------- #
# TC-0015 / TC-0016 state transitions
# --------------------------------------------------------------------------- #
def test_tc0015_success_state_transition_to_credit():
    validator, _, log = make_validator([make_account(balance="1000.00")])
    res = validator.process("tx", msg(amount="100.00", trans_type=2))
    assert res.state is State.CREDIT
    assert log.get("tx").state is State.CREDIT


def test_tc0016_audit_state_on_blacklist_and_insufficient_funds():
    v1, _, _ = make_validator([make_account(status="B")])
    assert v1.process("a", msg(trans_type=1)).state is State.AUDIT
    v2, _, _ = make_validator([make_account(balance="0.00")])
    assert v2.process("b", msg(amount="100.00", trans_type=2)).state is State.AUDIT


# --------------------------------------------------------------------------- #
# TC-0017 atomicity / optimistic locking
# --------------------------------------------------------------------------- #
class FailingRepo(InMemoryAccountRepository):
    def update_balance(self, account_id, new_balance, expected_version):
        raise ConcurrencyError("simulated stale version")


def test_tc0017_balance_update_failure_rolls_back():
    repo = FailingRepo([make_account(balance="1000.00")])
    validator, repo, _ = make_validator([], repo=repo)
    res = validator.process("tx", msg(amount="100.00", trans_type=2))
    assert res.success is False
    assert res.error_code is ErrorCode.SYSTEM_ERROR
    assert res.state is not State.CREDIT
    assert repo.find_by_account_id("ACC1").balance == Decimal("1000.00")  # rolled back


def test_optimistic_lock_rejects_stale_version():
    repo = InMemoryAccountRepository([make_account(balance="1000.00", version=5)])
    with pytest.raises(ConcurrencyError):
        repo.update_balance("ACC1", Decimal("900.00"), expected_version=4)
    # correct version succeeds and bumps the version
    updated = repo.update_balance("ACC1", Decimal("900.00"), expected_version=5)
    assert updated.balance == Decimal("900.00")
    assert updated.version == 6


# --------------------------------------------------------------------------- #
# TC-0018 transaction log completeness
# --------------------------------------------------------------------------- #
def test_tc0018_log_engine_receives_complete_details():
    validator, _, log = make_validator([make_account(balance="1000.00")])
    validator.process("tx18", msg(amount="100.00", trans_type=2))
    entry = log.get("tx18")
    assert entry.transaction_id == "tx18"
    assert entry.account_id == "ACC1"
    assert entry.trans_type == 2
    assert entry.original_amount == Decimal("100.00")
    assert entry.fee == Decimal("12.50")
    assert entry.vat == Decimal("18.00")
    assert entry.final_amount == Decimal("130.50")
    assert entry.state is State.CREDIT
    assert entry.error_code is ErrorCode.OK
    assert entry.timestamp is not None


# --------------------------------------------------------------------------- #
# Cross-cutting: idempotency, parsing, account-not-found, BR-0009, BR-0010
# --------------------------------------------------------------------------- #
def test_idempotency_no_double_debit():
    validator, repo, _ = make_validator([make_account(balance="1000.00")])
    first = validator.process("dup", msg(amount="100.00", trans_type=2))
    second = validator.process("dup", msg(amount="100.00", trans_type=2))
    assert second is first  # cached result returned
    assert repo.find_by_account_id("ACC1").balance == Decimal("869.50")  # debited once


def test_account_not_found():
    validator, _, _ = make_validator([])
    res = validator.process("tx", msg(account_id="GHOST", trans_type=1))
    assert res.error_code is ErrorCode.ACCOUNT_NOT_FOUND


def test_malformed_iso_message_rejected():
    validator, _, _ = make_validator([make_account()])
    res = validator.process("tx", {"3": "1"})  # missing amount + account
    assert res.error_code is ErrorCode.INVALID_FORMAT


def test_parse_iso8583_minor_units():
    from payment_processing_core import parse_iso8583

    parsed = parse_iso8583({4: "0000010050", 3: "2", 102: "ACC9"})
    assert parsed.amount == Decimal("100.50")
    assert parsed.trans_type == 2
    assert parsed.account_id == "ACC9"


def test_br0009_employee_parity():
    validator, _, _ = make_validator([make_account()])
    assert validator.br0009_validate_employee(4) == "P"  # even -> blocked
    assert validator.br0009_validate_employee(7) == "V"  # odd -> valid


def test_br0010_high_balance_report():
    accounts = [
        make_account(account_id="A1", balance="90000.00"),
        make_account(account_id="A2", balance="100.00"),
        make_account(account_id="A3", balance="80000.01"),
    ]
    validator, _, _ = make_validator(accounts)
    flagged = {a.account_id for a in validator.br0010_high_balance_report()}
    assert flagged == {"A1", "A3"}


def test_blacklist_takes_precedence_over_amount_validation():
    # BR-0001 must run before BR-0002/BR-0003.
    validator, _, _ = make_validator([make_account(status="B")])
    res = validator.process("tx", msg(amount="0.01", trans_type=1))
    assert res.error_code is ErrorCode.BLACKLISTED  # not INVALID_FORMAT


def test_repository_is_abstract():
    assert issubclass(InMemoryAccountRepository, AccountRepository)
