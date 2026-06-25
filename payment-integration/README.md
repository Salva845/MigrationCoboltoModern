# payment-integration

End-to-end **integration** slice for migration wave 1. It verifies that the
wave-1 services work together across the real HTTP boundary — it does **not**
reimplement any business logic. Two functional slices are covered:

- **BANK85** compliance/fraud validation — prompt 03 (INTEGRATE): "Process and
  validate a financial transaction with compliance and fraud controls", over
  `POST /transactions/validate`.
- **BANK74** fee/debit processing — prompt 06 (INTEGRATE): "Process a financial
  transaction with validation, fee calculation, and balance update", over
  `POST /transactions/process`.

Underlying implementations:

- [`payment-processing-core`](../payment-processing-core) — business rules, state
  machine, atomic debit, optimistic locking and idempotency (prompt 01 BANK85
  `TransactionValidator`; prompt 05 BANK74 `TransactionProcessor`).
- [`payment-message-processing`](../payment-message-processing) — the HTTP front
  end, request parsing and customer/employee resolution (prompt 02 BANK85;
  prompt 04 BANK74).

Each prompt's "out of scope" section assigns the implementations to the sibling
prompts; this slice is the seam between them. Python, no runtime third-party
deps.

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
tests/
  test_integration.py             BANK85: TC-0001..TC-0018 + BR-0009 / idempotency / concurrency
  test_processing_integration.py  BANK74: TC-0019..TC-0027 + timeout / high-risk / idempotency
```

The same `running_system(...)` boots one server exposing both endpoints, so
`sys.post_validate(...)` (BANK85) and `sys.post_process(...)` (BANK74) share a
single account repository and log engine.

## Run

```bash
pip install -e ../payment-processing-core
pip install -e ../payment-message-processing
pip install -e ".[dev]"
pytest -q                              # 41 integration tests
ruff check . && ruff format --check .
```

(Tests also resolve both siblings via `pythonpath` in `pyproject.toml`, so they
run without the editable installs too.)

## BANK85 coverage (TC-0001..TC-0018, `POST /transactions/validate`)

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

## BANK74 coverage (TC-0019..TC-0027, `POST /transactions/process`)

| Area | Cases |
| --- | --- |
| Happy path: flat 15.00 fee, total debit, state 30 | TC-0019 |
| Amount thresholds (min accepted, over-max / under-min → audit) | TC-0020..TC-0022 |
| Insufficient funds boundary (== passes, < rejected) | TC-0023, TC-0024 |
| High-risk scan (> 90,000.00, strict `>`) in log state | TC-0025 |
| Type-6 timeout: error, state 40, no debit | TC-0026 |
| Equivalence with legacy oracle | TC-0027 |
| BR-0016 removed (type 4 processed normally) | type-4 happy path |
| Error-code → HTTP status mapping | 200/400/404/409/504 |
| Idempotency across two HTTP calls | no double debit, single audit entry |
| Atomicity under 30 concurrent requests | no lost updates / negative balance |
| Config thresholds flow end-to-end (not hard-coded) | fee / high-risk override |
| Both slices share one repository | `/validate` + `/process` coexist |

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

### BANK74 specifics (prompt 06)

- **Error code → HTTP status** adds `WS-ERR-TIMEOUT` 504 (type-6 simulation) on
  top of `WS-OK` 200, `WS-ERR-INVALID-FORMAT` 400, `WS-ERR-ACCOUNT-NOT-FOUND`
  404, `WS-ERR-INSUFF-FUNDS` 409.
- **State machine**: success → `30` (credit); every error path and type 6 → `40`
  (audit/log), verified over the wire.
- **Slice isolation**: BANK74 charges a flat 15.00 fee with **no VAT**; BANK85
  adds VAT with no fixed fee. The two endpoints run on the same server and share
  one account repository without interfering.
