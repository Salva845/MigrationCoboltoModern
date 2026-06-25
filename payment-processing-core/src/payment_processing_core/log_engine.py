"""Transaction log engine (TRANS-LOG-ENGINE.CPY replacement).

Captures an immutable, queryable audit trail and provides idempotency lookups by
transaction id.
"""

from abc import ABC, abstractmethod

from .models import TransactionResult


class TransactionLogEngine(ABC):
    @abstractmethod
    def exists(self, transaction_id: str) -> bool:
        """Return True if a transaction id has already been logged."""

    @abstractmethod
    def get(self, transaction_id: str) -> TransactionResult | None:
        """Return the previously logged result for a transaction id, if any."""

    @abstractmethod
    def log_transaction(self, result: TransactionResult) -> None:
        """Append a result to the immutable audit trail."""


class InMemoryTransactionLogEngine(TransactionLogEngine):
    def __init__(self):
        self._log: list[TransactionResult] = []
        self._by_id: dict[str, TransactionResult] = {}

    def exists(self, transaction_id: str) -> bool:
        return transaction_id in self._by_id

    def get(self, transaction_id: str) -> TransactionResult | None:
        return self._by_id.get(transaction_id)

    def log_transaction(self, result: TransactionResult) -> None:
        if result.transaction_id in self._by_id:
            raise ValueError(f"transaction {result.transaction_id} already logged")
        self._log.append(result)
        self._by_id[result.transaction_id] = result

    @property
    def entries(self) -> list[TransactionResult]:
        return list(self._log)
