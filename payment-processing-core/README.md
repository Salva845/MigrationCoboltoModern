# payment-processing-core

Modernised replacement for the legacy COBOL **BANK85** program (migration wave 1,
prompt 01). Implements the core transaction validation and processing pipeline as
a stateless service: ISO 8583 parsing → multi-stage validation → fee/VAT
calculation → account debit → state transition → audit logging.

Written in Python (chosen for minimal LOC + native fixed-point `Decimal`). No
runtime third-party dependencies.

## Layout

```
src/payment_processing_core/
  config.py       RuleConfig — thresholds/rates (legacy SECURITY-RULES.CPY)
  errors.py       ErrorCode enum + internal TransactionError
  models.py       Account, TransactionResult, State machine
  iso8583.py      ISO 8583 field extraction (DE004 amount, type, account id)
  repository.py   AccountRepository abstraction + in-memory impl (optimistic lock)
  log_engine.py   TransactionLogEngine (immutable audit trail + idempotency)
  fraud.py        FraudScoringEngine (injectable; default heuristic)
  validator.py    TransactionValidator — BR-0001..BR-0010 + orchestration
  processing.py   TransactionProcessor — BANK74 fee/debit slice (BR-0011..BR-0018)
tests/            TC-0001..TC-0018 (validator) + TC-0019..TC-0027 (processor)
```

## Run

```bash
pip install -e ".[dev]"      # or: pip install pytest ruff
pytest -q                    # 48 tests
ruff check . && ruff format --check .
```

## Pipeline order (significant — preserved from BANK85)

1. ISO 8583 parse (reject malformed → `WS-ERR-INVALID-FORMAT`)
2. Account lookup (missing → `WS-ERR-ACCOUNT-NOT-FOUND`)
3. **BR-0001** blacklist (`status == 'B'` → `WS-ERR-BLACKLISTED`, state 40)
4. **BR-0002/BR-0003** amount thresholds (inclusive; out of bounds → `WS-ERR-INVALID-FORMAT`)
5. **BR-0004** SPEI from `NOMINA` blocked → `WS-ERR-AUTH-DENIED`
6. **BR-0006** SPEI fee (type 2 → 12.50, else 0)
7. **BR-0007** VAT = (amount + fee) × rate; final = amount + fee + VAT (no compounding)
8. **BR-0008** fraud score ≥ 60 → `WS-ERR-BLACKLISTED`
9. **BR-0005** insufficient funds → `WS-ERR-INSUFF-FUNDS`, state 40, **not debited**
10. atomic debit (optimistic lock) → state 30 (credit) → audit log

`BR-0009` (payroll employee-id parity) and `BR-0010` (high-balance flagging) are
discrete, off-critical-path methods.

## State machine

| State | Value | Meaning |
| --- | --- | --- |
| VALIDATE | 3200 | validation in progress / pre-debit rejection |
| DEBIT | 3400 | debit stage |
| CREDIT | 30 | posted (success) |
| AUDIT | 40 | blacklist or insufficient funds |

## Error codes (`ErrorCode`)

`WS-OK`, `WS-ERR-INVALID-FORMAT`, `WS-ERR-BLACKLISTED`, `WS-ERR-AUTH-DENIED`,
`WS-ERR-INSUFF-FUNDS`, `WS-ERR-ACCOUNT-NOT-FOUND`, `WS-ERR-TIMEOUT`,
`WS-ERR-SYSTEM-ERROR`.

## API contract

`TransactionValidator.process(transaction_id: str, message: dict | Iso8583Message) -> TransactionResult`

- **Idempotent:** replaying a `transaction_id` returns the cached result; the
  account is never debited twice.
- **Atomic debit:** balance update + credit transition go through
  `AccountRepository.update_balance(..., expected_version)`; a stale version or
  failure rolls back (no debit) and yields `WS-ERR-SYSTEM-ERROR`.
- **Money:** all monetary maths use `Decimal` (2dp, `ROUND_HALF_UP`).

## BANK74 processing slice (prompt 05)

`processing.py` is a **separate functional slice** migrated from legacy COBOL
**BANK74** (wave 1, prompt 05): "Process a financial transaction with validation,
fee calculation, and balance update". It is distinct from the BANK85 compliance
flow above — there is no VAT, the fee is a flat **15.00**, and the high-risk
threshold is **90,000.00**. Only the shared abstractions (ISO parser, `Account`,
`State`, `ErrorCode`, `AccountRepository`) are reused. The HTTP transport wrapper
for this slice lives in `payment-message-processing` (prompt 04); cross-service
wiring is prompt 06.

### API contract

`TransactionProcessor.process(transaction_id: str, message: dict | Iso8583Message) -> ProcessingResult`

`ProcessingResult.to_dict()` serialises the outcome (monetary values as 2dp
strings) for an equivalence comparison against the legacy oracle (TC-0027).

### Pipeline order (preserved from BANK74)

| State | Value | Step |
| --- | --- | --- |
| VAL | 05¹ | account lookup, **BR-0011** min / **BR-0012** max thresholds |
| DEBIT | 20¹ | **BR-0013** total = amount + 15.00 fee, **BR-0014** sufficiency |
| CREDIT | 30 | **BR-0017** type 6 → timeout *before* write (no debit), else **BR-0015** atomic debit |
| LOG | 40 | **BR-0018** high-risk scan (balance > 90,000.00), append audit entry |

¹ The legacy VAL/DEBIT stage labels map onto the shared `State` enum values
`VALIDATE` (3200) / `DEBIT` (3400); all error paths terminate in `AUDIT` (40).

### Business rules

| Rule | Behaviour |
| --- | --- |
| BR-0011 | reject `amount < min` → `WS-ERR-INVALID-FORMAT`, state 40 |
| BR-0012 | reject `amount > max` → `WS-ERR-INVALID-FORMAT`, state 40 |
| BR-0013 | total debit = `amount + 15.00` (fee added **before** sufficiency check) |
| BR-0014 | `balance < total` → `WS-ERR-INSUFF-FUNDS`, state 40, **not debited** |
| BR-0015 | atomic debit via optimistic lock (no partial update) |
| BR-0016 | **REMOVED** — type-4 auth denial was test-only simulation code |
| BR-0017 | type 6 → `WS-ERR-TIMEOUT`, state 40; checked **before** the debit so the balance is untouched |
| BR-0018 | flag every account with `balance > 90,000.00` during logging (`>`, not `>=`); informational, never blocks |

Idempotency (no double-debit on replay), atomic debit, and `Decimal`-only maths
(2dp, `ROUND_HALF_UP`) hold for this slice too.

## Assumptions

The legacy COBOL copybooks (`SECURITY-RULES.CPY`, `DATABASE.CPY`, …) were not
included with this slice, so the threshold values in `config.py`
(`min=1.00`, `max=1000000.00`, `vat=0.16`, `spei_fee=12.50`) and in
`ProcessingConfig` (BANK74: `min=1.00`, `max=1000000.00`, `fixed_fee=15.00`,
`high_risk_threshold=90000.00`, `timeout_trans_type=6`) are documented
assumptions isolated for a later equivalence pass. Per the prompt's noted
spec contradiction, the fraud block is implemented as **score ≥ 60** (the
acceptance test is authoritative).
