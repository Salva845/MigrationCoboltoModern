# payment-message-processing

Modernised replacement for the legacy COBOL **BANK85** orchestrator (migration
wave 1, prompt 02). A REST front end that receives ISO 8583 financial messages,
validates them through the ten business rules, debits the account atomically,
transitions the transaction through the state machine and returns a success or
error response.

It is a **thin HTTP wrapper over** [`payment-processing-core`](../payment-processing-core)
(prompt 01): the ten business rules, state machine, atomic debit, optimistic
locking and idempotency live in the core `TransactionValidator` and are **not
reimplemented here**. This slice only adds the HTTP transport, request parsing,
customer/employee resolution (BR-0009) and the response/HTTP-status mapping.

Python, no runtime third-party dependencies (stdlib `http.server`).

## Layout

```
src/payment_message_processing/
  config.py       build_message_config — overrides fraud op to strict > 60 (prompt 02)
  customer.py     CustomerRepository abstraction + in-memory impl (PR-EMP-ID)
  parser.py       request envelope + ISO 8583 field extraction (ISO-PARSER.CPY)
  dto.py          TransactionResponse body + ErrorCode -> HTTP status map
  controller.py   TransactionValidationController (orchestrates core validator)
  http_app.py     POST /transactions/validate (stdlib http.server)
tests/            TC-0001..TC-0018 + BR-0009/idempotency/edge-case/HTTP coverage
```

## Run

```bash
pip install -e ../payment-processing-core    # sibling slice (library)
pip install -e ".[dev]"
pytest -q                                    # 38 tests
ruff check . && ruff format --check .
```

Start the service:

```python
from payment_processing_core import Account, InMemoryAccountRepository
from payment_message_processing import TransactionValidationController, build_server

ctrl = TransactionValidationController(
    account_repository=InMemoryAccountRepository([
        Account("ACC1", "A", __import__("decimal").Decimal("1000.00"), 0, "CHECKING"),
    ]),
)
build_server(ctrl, host="127.0.0.1", port=8080).serve_forever()
```

## API contract

`POST /transactions/validate` — `Content-Type: application/json`

Request:

```json
{
  "transaction_id": "T1",
  "customer_id": "CUST1",
  "message": { "4": "10000", "3": 2, "102": "ACC1" }
}
```

- `transaction_id` (required) — idempotency key.
- `message` — ISO 8583 data elements: `4` = amount in minor units (cents),
  `3` = transaction/processing type, `102` = account id. May also be supplied at
  the top level. UTF-8 preserved end to end.
- `customer_id` (optional) — top level or ISO field `103`. When present, the
  customer is looked up for the BR-0009 payroll check; if the customer is not
  found, BR-0009 is skipped.

Success response (HTTP 200):

```json
{
  "transaction_id": "T1", "success": true, "status": "WS-OK", "state": 30,
  "account_id": "ACC1", "trans_type": 2, "original_amount": "100.00",
  "fee": "12.50", "vat": "18.00", "final_amount": "130.50",
  "message": "transaction posted", "duplicate": false, "timestamp": "..."
}
```

Error response carries `success: false`, `error_code`, `state` (40 on
blacklist/insufficient funds) and a `message`. Monetary fields are JSON strings
to avoid floating-point precision loss.

## Rule order (preserved from BANK85, prompt 02 section 8)

`BR-0001 → BR-0002 → BR-0003 → BR-0004 → BR-0006 → BR-0007 → BR-0008 → BR-0005 →
BR-0009 → BR-0010`. The first rejecting rule stops processing. BR-0010
(high-balance flagging) is a monitoring side-effect run after the blocking rules
and never affects the outcome.

## Error code → HTTP status

| ErrorCode | HTTP |
| --- | --- |
| `WS-OK` | 200 |
| `WS-ERR-INVALID-FORMAT` | 400 |
| `WS-ERR-BLACKLISTED` | 403 |
| `WS-ERR-AUTH-DENIED` | 403 |
| `WS-ERR-INSUFF-FUNDS` | 409 |
| `WS-ERR-ACCOUNT-NOT-FOUND` | 404 |
| `WS-ERR-SYSTEM-ERROR` | 500 |

## State machine

`3200` (validate) → `3400` (debit) → `30` (credit, success); any rejection routes
to `40` (audit) for blacklist / insufficient funds.

## Coherence with prompt 01

- Rules, state values and error codes are imported from `payment-processing-core`
  — single source of truth, no duplication.
- **Fraud boundary differs by slice.** Prompt 01 blocks at score **≥ 60**
  (TC-0006: 60 blocks); prompt 02 blocks at score **> 60** (TC-0006: 60 allows).
  This is selected via the new backward-compatible `RuleConfig.fraud_block_strict`
  flag (`build_message_config` sets it `True`); the core default is unchanged.
- **BR-0009** (payroll employee-id parity) runs inside the core pipeline after
  BR-0005 when an `employee_id` is supplied; an even id (incl. 0) sets status
  `'P'` and rejects with `WS-ERR-AUTH-DENIED`. Non-payroll transactions (no
  customer) skip it — preserving prompt 01 behaviour.
