"""Modernised payment-processing-core service (migrated from COBOL BANK85).

Wave 1 / prompt 01: process and validate a financial transaction with
compliance and fraud controls.
"""

from .config import RuleConfig
from .errors import ErrorCode
from .fraud import DefaultFraudScoringEngine, FraudScoringEngine
from .iso8583 import Iso8583Message, parse_iso8583
from .log_engine import InMemoryTransactionLogEngine, TransactionLogEngine
from .models import Account, State, TransactionResult
from .processing import (
    ProcessingConfig,
    ProcessingResult,
    TransactionProcessor,
)
from .repository import AccountRepository, ConcurrencyError, InMemoryAccountRepository
from .validator import TransactionValidator

__all__ = [
    "Account",
    "AccountRepository",
    "ConcurrencyError",
    "DefaultFraudScoringEngine",
    "ErrorCode",
    "FraudScoringEngine",
    "InMemoryAccountRepository",
    "InMemoryTransactionLogEngine",
    "Iso8583Message",
    "ProcessingConfig",
    "ProcessingResult",
    "RuleConfig",
    "State",
    "TransactionLogEngine",
    "TransactionProcessor",
    "TransactionResult",
    "TransactionValidator",
    "parse_iso8583",
]
