# payment-integration

End-to-end **integration** slice for migration wave 1, prompt 03 (INTEGRATE):
"Process and validate a financial transaction with compliance and fraud
controls". It verifies that the two wave-1 services work together across the real
HTTP boundary — it does **not** reimplement any business logic.

- [`payment-processing-core`](../payment-processing-core) (prompt 01) — the ten
  business rules, state machine, atomic debit, optimistic locking and
  idempotency.
- [`payment-message-processing`](../payment-message-processing) (prompt 02) — the
  HTTP front end (`POST /transactions/validate`), request parsing and
  customer/employee resolution.

The prompt's "out of scope" section assigns the implementations to prompts 01 and
02; this slice is the seam between them. Python, no runtime third-party deps.

## What it does

`payment_integration.running_system(...)` boots the prompt-02 HTTP server (which
delegates to the prompt-01 `TransactionValidator`) on an ephemeral port and yields
a client plus the **shared** account repository, customer repository and
transaction log engine. Tests POST JSON to the live socket and then assert
cross-service effects on those shared instances:

```python
from payment_integration import account, request_payload, running_system

with running_system([account(balance="1000.00")]) as sys:
    status, body = sys.post_validate(request_payload(amount="100.00"))
    assert status == 200 and body["final_amount"] == "116.00"
    # debit observed on the shared repository the service actually mutated
    assert sys.account_repository.find_by_account_id("ACC1").balance == ...
```

## Layout

```
src/payment_integration/
  harness.py   running_system() wiring both slices over real HTTP + IntegrationSystem client
tests/         TC-0001..TC-0018 end-to-end + BR-0009 / idempotency / concurrency / mapping
```

## Run

```bash
pip install -e ../payment-processing-core
pip install -e ../payment-message-processing
pip install -e ".[dev]"
pytest -q                              # 24 integration tests
ruff check . && ruff format --check .
```

(Tests also resolve both siblings via `pythonpath` in `pyproject.toml`, so they
run without the editable installs too.)

## Coverage (TC-0001..TC-0018, over HTTP)

| Area | Cases |
| --- | --- |
| Happy path, fee/VAT/total, credit state | TC-0001, TC-0008, TC-0014, TC-0015 |
| Amount thresholds (inclusive bounds) | TC-0002..TC-0005 |
| Fraud boundary (strict `> 60`: 60 allows) | TC-0006, TC-0007 (+ 61 blocks) |
| Insufficient funds (no debit, audit state) | TC-0009, TC-0010 |
| Blacklist → audit state | TC-0011, TC-0016 |
| NOMINA SPEI restriction | TC-0012, TC-0013 |
| Atomicity under 30 concurrent requests | TC-0017 |
| Audit-log completeness | TC-0018 |
| BR-0009 payroll parity via customer lookup | even blocks / odd allows / unknown skips |
| Idempotency across two HTTP calls | no double debit, `duplicate` flag |
| Error-code → HTTP status mapping | 200/400/403/404/409 |

## Cross-service contract verified

- **Error code → HTTP status** is consistent end to end: `WS-OK` 200,
  `WS-ERR-INVALID-FORMAT` 400, `WS-ERR-BLACKLISTED` / `WS-ERR-AUTH-DENIED` 403,
  `WS-ERR-ACCOUNT-NOT-FOUND` 404, `WS-ERR-INSUFF-FUNDS` 409.
- **State machine** values cross the boundary unchanged: `3200 → 3400 → 30`
  (success); `→ 40` (audit) on blacklist / insufficient funds.
- **Numeric precision**: monetary fields travel as JSON strings; `Decimal`
  equality is asserted on the shared repository / log after each call.
- **Fraud boundary** matches prompt 02 (`> 60`, so score 60 is allowed) because
  the HTTP front end configures the core with `fraud_block_strict=True`.
- **Idempotency** holds across distinct HTTP requests (same `transaction_id`):
  the second call returns `duplicate: true` and the account is debited once.
