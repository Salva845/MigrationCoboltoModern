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
tests/            TC-0001..TC-0018 + business-rule/edge-case coverage
```

## Run

```bash
pip install -e ".[dev]"      # or: pip install pytest ruff
pytest -q                    # 28 tests
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
`WS-ERR-INSUFF-FUNDS`, `WS-ERR-ACCOUNT-NOT-FOUND`, `WS-ERR-SYSTEM-ERROR`.

## API contract

`TransactionValidator.process(transaction_id: str, message: dict | Iso8583Message) -> TransactionResult`

- **Idempotent:** replaying a `transaction_id` returns the cached result; the
  account is never debited twice.
- **Atomic debit:** balance update + credit transition go through
  `AccountRepository.update_balance(..., expected_version)`; a stale version or
  failure rolls back (no debit) and yields `WS-ERR-SYSTEM-ERROR`.
- **Money:** all monetary maths use `Decimal` (2dp, `ROUND_HALF_UP`).

## Assumptions

The legacy COBOL copybooks (`SECURITY-RULES.CPY`, `DATABASE.CPY`, …) were not
included with this slice, so the threshold values in `config.py`
(`min=1.00`, `max=1000000.00`, `vat=0.16`, `spei_fee=12.50`) are documented
assumptions isolated for a later equivalence pass. Per the prompt's noted
spec contradiction, the fraud block is implemented as **score ≥ 60** (the
acceptance test is authoritative).
