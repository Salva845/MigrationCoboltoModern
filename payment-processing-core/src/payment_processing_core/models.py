"""Domain models and transaction state machine."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import IntEnum

from .errors import ErrorCode


class State(IntEnum):
    """Transaction states preserved from BANK85."""

    VALIDATE = 3200  # 3200-STATE-VALIDATE
    DEBIT = 3400  # 3400-STATE-DEBIT
    CREDIT = 30  # posting / success
    AUDIT = 40  # blacklist / insufficient funds


@dataclass
class Account:
    account_id: str
    status: str  # DB-ACC-ST ('B' == blacklisted)
    balance: Decimal  # DB-ACC-BAL
    risk_level: int  # DB-ACC-RISK
    account_type: str  # WS-ACC-TYPE (e.g. 'NOMINA')
    version: int = 0  # optimistic-locking version


@dataclass
class TransactionResult:
    transaction_id: str
    account_id: str
    trans_type: int
    success: bool
    state: State
    error_code: ErrorCode
    original_amount: Decimal
    fee: Decimal = Decimal("0.00")
    vat: Decimal = Decimal("0.00")
    final_amount: Decimal = Decimal("0.00")
    message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
