"""End-to-end harness wiring the wave-1 slices together.

The INTEGRATE prompts do not reimplement any business logic; the rules live in
payment-processing-core and the HTTP front end in payment-message-processing.
This module composes them into a running system so the integration tests can
drive the *real* HTTP boundary and then assert cross-service effects against the
shared account repository and transaction log engine.

Two functional slices are exercised over the same server:

- BANK85 compliance/fraud validation (prompt 03): POST /transactions/validate.
- BANK74 fee/debit processing (prompt 06): POST /transactions/process, delegating
  to the core ``TransactionProcessor`` (prompt 05) behind its controller (prompt
  04). No VAT, a flat 15.00 fee and a 90,000.00 high-risk threshold.
"""

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from payment_message_processing import (
    Customer,
    InMemoryCustomerRepository,
    ProcessingConfig,
    TransactionProcessingController,
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
PROCESS_PATH = "/transactions/process"


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
    """Build a request JSON body (ISO 8583 envelope) for either endpoint."""
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
    processing_controller: TransactionProcessingController
    account_repository: AccountRepository
    customer_repository: InMemoryCustomerRepository
    log_engine: TransactionLogEngine

    def _post(self, path: str, payload: object, retries: int = 4) -> tuple[int, dict]:
        """POST a JSON body to ``path``; return (status, parsed body).

        The stdlib ``ThreadingHTTPServer`` can occasionally reset a connection
        under heavy concurrent load (the concurrency stress tests fire many
        requests at once). Such resets are transient transport failures, so the
        request is retried a few times; this is safe because both pipelines are
        idempotent (a repeated ``transaction_id`` never double-debits).
        """
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req) as resp:  # noqa: S310 - localhost test server.
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a JSON body.
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except (ConnectionError, http.client.RemoteDisconnected, urllib.error.URLError) as exc:
                last_exc = exc
                time.sleep(0.02 * (attempt + 1))
        raise AssertionError(f"request failed after {retries} attempts: {last_exc}")

    def post_validate(self, payload: object, retries: int = 4) -> tuple[int, dict]:
        """POST to /transactions/validate (BANK85 slice); return (status, body)."""
        return self._post(VALIDATE_PATH, payload, retries)

    def post_process(self, payload: object, retries: int = 4) -> tuple[int, dict]:
        """POST to /transactions/process (BANK74 slice); return (status, body)."""
        return self._post(PROCESS_PATH, payload, retries)


@contextmanager
def running_system(
    accounts: list[Account] | None = None,
    customers: list[Customer] | None = None,
    fraud: FraudScoringEngine | None = None,
    *,
    account_repository: AccountRepository | None = None,
    log_engine: TransactionLogEngine | None = None,
    processing_config: ProcessingConfig | None = None,
) -> Iterator[IntegrationSystem]:
    """Boot the wired system on an ephemeral port and yield a client.

    The HTTP server (payment-message-processing) delegates to the core
    TransactionValidator and TransactionProcessor (payment-processing-core); all
    share the supplied repository and log engine so effects are observable from
    the test. The same server exposes both wave-1 slices: /transactions/validate
    (BANK85) and /transactions/process (BANK74).
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
    processing_controller = TransactionProcessingController(
        account_repository=repo,
        config=processing_config,
    )

    server = build_server(
        controller,
        host="127.0.0.1",
        port=0,
        processing_controller=processing_controller,
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield IntegrationSystem(
            base_url=f"http://127.0.0.1:{port}",
            controller=controller,
            processing_controller=processing_controller,
            account_repository=repo,
            customer_repository=cust_repo,
            log_engine=log,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
