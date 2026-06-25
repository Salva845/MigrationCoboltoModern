"""End-to-end harness wiring the two wave-1 slices together.

Prompt 03 (INTEGRATE) does not reimplement any business logic; the rules live in
payment-processing-core (prompt 01) and the HTTP front end in
payment-message-processing (prompt 02). This module composes both into a running
system so the integration tests can drive the *real* HTTP boundary
(POST /transactions/validate) and then assert cross-service effects against the
shared account repository and transaction log engine.
"""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from payment_message_processing import (
    Customer,
    InMemoryCustomerRepository,
    TransactionValidationController,
    build_server,
)
from payment_processing_core import (
    Account,
    AccountRepository,
    FraudScoringEngine,
    InMemoryAccountRepository,
    InMemoryTransactionLogEngine,
    TransactionLogEngine,
)

VALIDATE_PATH = "/transactions/validate"


def account(
    account_id: str = "ACC1",
    *,
    status: str = "A",
    balance: str = "100000.00",
    risk: int = 0,
    acc_type: str = "CHECKING",
    version: int = 0,
) -> Account:
    """Build a core Account (DB-ACC-* fields) for test fixtures."""
    return Account(
        account_id=account_id,
        status=status,
        balance=Decimal(balance),
        risk_level=risk,
        account_type=acc_type,
        version=version,
    )


def minor(amount: str) -> str:
    """Major units -> ISO 8583 DE004 minor units (cents) as a digit string."""
    return str(int((Decimal(amount) * 100).to_integral_value()))


def request_payload(
    *,
    txn_id: str = "T1",
    amount: str = "100.00",
    trans_type: int = 0,
    account_id: str = "ACC1",
    customer_id: str | None = None,
) -> dict:
    """Build a POST /transactions/validate JSON body (ISO 8583 envelope)."""
    payload: dict = {
        "transaction_id": txn_id,
        "message": {4: minor(amount), 3: trans_type, 102: account_id},
    }
    if customer_id is not None:
        payload["customer_id"] = customer_id
    return payload


@dataclass
class IntegrationSystem:
    """A live two-service system reachable over real HTTP.

    The repository / log engine references are the same instances the running
    service mutates, so tests can assert balance debits, state transitions and
    audit-log completeness after a request returns.
    """

    base_url: str
    controller: TransactionValidationController
    account_repository: AccountRepository
    customer_repository: InMemoryCustomerRepository
    log_engine: TransactionLogEngine

    def post_validate(self, payload: object) -> tuple[int, dict]:
        """POST a JSON body to /transactions/validate; return (status, body)."""
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{VALIDATE_PATH}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:  # noqa: S310 - localhost test server.
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a JSON body.
            return exc.code, json.loads(exc.read().decode("utf-8"))


@contextmanager
def running_system(
    accounts: list[Account] | None = None,
    customers: list[Customer] | None = None,
    fraud: FraudScoringEngine | None = None,
    *,
    account_repository: AccountRepository | None = None,
    log_engine: TransactionLogEngine | None = None,
) -> Iterator[IntegrationSystem]:
    """Boot the wired system on an ephemeral port and yield a client.

    The HTTP server (payment-message-processing) delegates to the core
    TransactionValidator (payment-processing-core); both share the supplied
    repository and log engine so effects are observable from the test.
    """
    repo = account_repository or InMemoryAccountRepository(accounts or [account()])
    cust_repo = InMemoryCustomerRepository(customers or [])
    log = log_engine or InMemoryTransactionLogEngine()
    controller = TransactionValidationController(
        account_repository=repo,
        customer_repository=cust_repo,
        log_engine=log,
        fraud_engine=fraud,
    )

    server = build_server(controller, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield IntegrationSystem(
            base_url=f"http://127.0.0.1:{port}",
            controller=controller,
            account_repository=repo,
            customer_repository=cust_repo,
            log_engine=log,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
