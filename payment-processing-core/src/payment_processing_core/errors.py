"""Error codes preserved verbatim from the legacy BANK85 program."""

from enum import Enum


class ErrorCode(str, Enum):
    OK = "WS-OK"
    INVALID_FORMAT = "WS-ERR-INVALID-FORMAT"
    BLACKLISTED = "WS-ERR-BLACKLISTED"
    AUTH_DENIED = "WS-ERR-AUTH-DENIED"
    INSUFF_FUNDS = "WS-ERR-INSUFF-FUNDS"
    ACCOUNT_NOT_FOUND = "WS-ERR-ACCOUNT-NOT-FOUND"
    SYSTEM_ERROR = "WS-ERR-SYSTEM-ERROR"


class TransactionError(Exception):
    """Raised internally by a business rule to short-circuit processing.

    Carries the error code, the audit state to transition to (if any), and a
    human-readable reason for the immutable audit trail.
    """

    def __init__(self, code: ErrorCode, reason: str, audit_state: int | None = None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.audit_state = audit_state
