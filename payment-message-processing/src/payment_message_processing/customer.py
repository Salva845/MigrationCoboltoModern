"""Customer persistence abstraction (CUSTOMER.CPY replacement).

Supplies the employee id consumed by BR-0009 (payroll eligibility). Kept in this
slice because customer ownership belongs to the payment-message-processing
bounded context; payment-processing-core only knows about accounts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Customer:
    customer_id: str
    employee_id: int | None = None  # PR-EMP-ID


class CustomerRepository(ABC):
    @abstractmethod
    def find_by_customer_id(self, customer_id: str) -> Customer | None:
        """Return the customer or None if it does not exist."""


class InMemoryCustomerRepository(CustomerRepository):
    def __init__(self, customers: list[Customer] | None = None):
        self._customers: dict[str, Customer] = {c.customer_id: c for c in (customers or [])}

    def find_by_customer_id(self, customer_id: str) -> Customer | None:
        stored = self._customers.get(customer_id)
        return Customer(**vars(stored)) if stored is not None else None
