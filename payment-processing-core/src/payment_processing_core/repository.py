"""Account persistence abstraction (DATABASE.CPY replacement).

The service never touches the data store directly; all account reads and balance
updates go through this interface. A PostgreSQL implementation can be supplied in
production; the in-memory implementation backs the test suite and keeps the
balance read-modify-write atomic with optimistic locking.
"""

import threading
from abc import ABC, abstractmethod
from decimal import Decimal

from .models import Account


class ConcurrencyError(Exception):
    """Raised when an optimistic-lock version check fails."""


class AccountRepository(ABC):
    @abstractmethod
    def find_by_account_id(self, account_id: str) -> Account | None:
        """Return the account or None if it does not exist."""

    @abstractmethod
    def update_balance(
        self, account_id: str, new_balance: Decimal, expected_version: int
    ) -> Account:
        """Atomically set the balance iff the stored version matches.

        Raises ConcurrencyError on a stale version and KeyError if missing.
        """

    @abstractmethod
    def all_accounts(self) -> list[Account]:
        """Return every account (used by the BR-0010 risk report batch)."""


class InMemoryAccountRepository(AccountRepository):
    def __init__(self, accounts: list[Account] | None = None):
        self._lock = threading.Lock()
        self._accounts: dict[str, Account] = {a.account_id: a for a in (accounts or [])}

    def find_by_account_id(self, account_id: str) -> Account | None:
        with self._lock:
            stored = self._accounts.get(account_id)
            if stored is None:
                return None
            # Return a copy so callers cannot mutate persisted state directly.
            return Account(**vars(stored))

    def update_balance(
        self, account_id: str, new_balance: Decimal, expected_version: int
    ) -> Account:
        with self._lock:
            stored = self._accounts.get(account_id)
            if stored is None:
                raise KeyError(account_id)
            if stored.version != expected_version:
                raise ConcurrencyError(
                    f"stale version for {account_id}: "
                    f"expected {expected_version}, found {stored.version}"
                )
            stored.balance = new_balance
            stored.version += 1
            return Account(**vars(stored))

    def all_accounts(self) -> list[Account]:
        with self._lock:
            return [Account(**vars(a)) for a in self._accounts.values()]
