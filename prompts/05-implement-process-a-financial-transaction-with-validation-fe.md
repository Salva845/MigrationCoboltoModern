[← Playbook index](../README.md)
 · [Service: payment-processing-core](../services/payment-processing-core.md)

# Migration prompt — Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-processing-core

> You are migrating one functional slice of a legacy system to its modern replacement. Implement **only** what this prompt covers; preserve behavior exactly. Sibling slices are handled by other prompts (see *Out of scope*).

## 1. Objective

Implement a transaction processing service in payment-processing-core that receives ISO 8583 formatted transactions, validates amounts against policy thresholds (minimum and maximum), calculates a fixed 15.00 fee, checks account balance sufficiency, debits the account atomically, detects high-risk accounts (balance > 90,000.00), logs all outcomes, and returns transaction status to the caller. This service must preserve exact numeric calculations, state transitions (40 for logging), error codes (WS-ERR-INVALID-FORMAT, WS-ERR-INSUFF-FUNDS, WS-ERR-TIMEOUT), and the complete exception handling matrix from the legacy COBOL system.

## 2. Target

- **Service:** payment-processing-core
- **Bounded context:** ISO 8583 payment message processing, transaction logging, and security rule enforcement for financial transactions
- **Tech stack:** (use the project default)
- **Data store:** PostgreSQL
- **Shared data (coordinate ownership):** ACCOUNT, CONSTANTS, COREVARS, CUSTOMER, DATABASE, DATETIME, ISO-BITMAP-LOGIC, ISO-PARSER, ISO8583, MESSAGES, REPORT-TEMPLATES, REPORTS, SECURITY-RULES, SYSTEM-SPECS, TRANS-LOG-ENGINE, VALIDATION
- **Migration wave:** 1

## 3. Functionality to preserve

A transaction processing capability that validates transaction amounts against policy thresholds, calculates fees, checks account sufficiency, updates account balances, and flags high-risk accounts. The system enforces minimum and maximum transaction limits, applies a fixed fee, prevents overdrafts, and maintains compliance monitoring through risk detection.

**Main flow (MUST preserve):**

1. Receive and parse incoming transaction (ISO 8583 format inferred from copybook references)
2. Validate transaction amount meets minimum threshold (WS-RULE-MIN-AMOUNT)
3. Validate transaction amount does not exceed maximum threshold (WS-RULE-MAX-TRANS)
4. Calculate total debit amount by adding fixed fee of 15.00 to transaction amount
5. Retrieve account balance from database
6. Validate account has sufficient funds to cover debit amount plus fee
7. Debit account balance by total amount (transaction + fee)
8. Scan all accounts for high-risk balances exceeding 90,000.00 and flag for reporting
9. Log transaction with final status
10. Return transaction result to caller

**Exception handling (MUST preserve):**

- Transaction amount below minimum threshold: set error status WS-ERR-INVALID-FORMAT, transition to logging state 40
- Transaction amount exceeds maximum threshold: set error status WS-ERR-INVALID-FORMAT, transition to logging state 40
- Insufficient account balance: set error status WS-ERR-INSUFF-FUNDS, transition to logging state 40, do not debit account
- Timeout during credit processing (type 6): set error status WS-ERR-TIMEOUT, transition to logging state 40
- Authorization failure (type 4): reject transaction (test simulation rule)

**Business rules (MUST preserve exactly):**

- **BR-0011 Transaction Amount Minimum Threshold Validation** — Validates that the transaction amount meets or exceeds a minimum threshold (WS-RULE-MIN-AMOUNT). This is a business policy to prevent processing of trivial or below-minimum transactions.
  - Logic: If WS-TEMP-AMOUNT is less than WS-RULE-MIN-AMOUNT, set status code to WS-ERR-INVALID-FORMAT, which causes the transaction to fail and transition to state 40 (LOG).
  - Triggers when: During state 05 (VAL) when 480-RULES-74 is performed after account is found
  - Exceptions: No explicit exception handling; invalid format error is set and transaction proceeds to failure logging.
- **BR-0012 Transaction Amount Maximum Threshold Validation** — Validates that the transaction amount does not exceed a maximum limit (WS-RULE-MAX-TRANS). This is a business policy to enforce transaction size caps, likely for risk or regulatory reasons.
  - Logic: If WS-TEMP-AMOUNT exceeds WS-RULE-MAX-TRANS, set status code to WS-ERR-INVALID-FORMAT, which causes the transaction to fail and transition to state 40 (LOG).
  - Triggers when: During state 05 (VAL) when 480-RULES-74 is performed after account is found
  - Exceptions: No explicit exception handling; invalid format error is set and transaction proceeds to failure logging.
- **BR-0013 Debit Amount Calculation with Fixed Fee** — Calculates the total debit amount by adding a fixed fee of 15.00 to the transaction amount. This implements a transaction fee policy.
  - Logic: Compute WS-TEMP-AMOUNT as WS-ISO-FLD-004-AMOUNT plus 15.00 (fixed fee). This total is then used for balance sufficiency check and subsequent debit.
  - Triggers when: During state 20 (DEBIT) when processing a transaction that passed validation and authorization
  - Exceptions: No exception handling for overflow or invalid amounts; assumes numeric validity.
- **BR-0014 Insufficient Funds Check** — Validates that the account has sufficient balance to cover the total debit amount (transaction plus fee). This is a core business rule preventing overdrafts.
  - Logic: If DB-ACC-BAL(ACC-IDX) is less than WS-TEMP-AMOUNT, set status code to WS-ERR-INSUFF-FUNDS and transition to state 40 (LOG). Otherwise, proceed to credit state 30.
  - Triggers when: During state 20 (DEBIT) after fee-inclusive amount is calculated
  - Exceptions: Transaction fails and is logged as failed; no partial debit or overdraft is permitted.
- **BR-0015 Account Balance Debit** — Subtracts the total debit amount (transaction plus fee) from the account balance. This is the core ledger update operation.
  - Logic: Subtract WS-TEMP-AMOUNT from DB-ACC-BAL(ACC-IDX) to reflect the transaction and fee deduction.
  - Triggers when: During state 20 (DEBIT) when balance is sufficient
  - Exceptions: No exception handling; assumes numeric validity and that balance check has already passed.
- **BR-0018 High-Risk Account Detection** — Identifies accounts with balances exceeding 90,000.00 as high-risk and flags them in reporting. This is a risk management policy for portfolio monitoring.
  - Logic: Iterate through all accounts. If DB-ACC-BAL(WS-IDX-1) exceeds 90,000.00, display a high-risk alert with the account number.
  - Triggers when: During state 40 (LOG) when 650-RISK-74 analysis is performed in the reporting section
  - Exceptions: No exception handling; alerts are informational only and do not block transactions.
- **BR-0017 Timeout Simulation for Credit State** — Simulates a timeout error during credit processing when transaction type is 6. This is a test/simulation rule for error handling scenarios.
  - Logic: If WS-TRANS-TYPE = 6, set status code to WS-ERR-TIMEOUT. Regardless of success or failure, transition to state 40 (LOG).
  - Triggers when: During state 30 (CREDIT) when WS-TRANS-TYPE equals 6
  - Exceptions: Simulated error; in production, timeout would indicate a communication or processing delay.
- **BR-0016 Authorization Denial for Simulated Error** — Simulates an authorization failure when transaction type is 4. This is a test/simulation rule, not a production business rule, but implements conditional authorization logic.
  - Logic: Type 4 rejection logic identified as test code unsuitable for production; recommended for removal during modernization.
  - Triggers when: During state 10 (AUTH) when WS-TRANS-TYPE equals 4
  - Exceptions: Simulated error; in production, authorization would be checked against external systems.

## 4. Source references (legacy)

- `sources/banamex-cobol/SECURITY-RULES.CPY` — **SECURITY-RULES** (cobol-copybook)
- `sources/banamex-cobol/DATABASE.CPY` — **DATABASE** (cobol-copybook)
- `sources/banamex-cobol/REPORTS.CPY` — **REPORTS** (cobol-copybook)
- `sources/banamex-cobol/SYSTEM-SPECS.CPY` — **SYSTEM-SPECS** (cobol-copybook)
- `sources/banamex-cobol/REPORT-TEMPLATES.CPY` — **REPORT-TEMPLATES** (cobol-copybook)
- `sources/banamex-cobol/DATETIME.CPY` — **DATETIME** (cobol-copybook)
- `sources/banamex-cobol/TRANS-LOG-ENGINE.CPY` — **TRANS-LOG-ENGINE** (cobol-copybook)
- `sources/banamex-cobol/ISO-BITMAP-LOGIC.CPY` — **ISO-BITMAP-LOGIC** (cobol-copybook)

_See the DeepWiki bundle's `modules/` pages for full documentation of each._

## 5. Input / output contract

_No structured I/O captured; infer from source + business rules._

## 6. Dependencies & integration

_No cross-service dependencies detected for this slice._

## 7. Acceptance criteria

The implementation must pass these test cases:

- **TC-0019** [FUNCTIONAL] Happy path: valid transaction processes successfully with fee calculation and balance update
- **TC-0020** [BOUNDARY] Boundary: transaction amount equals minimum threshold
- **TC-0021** [BOUNDARY] Boundary: transaction amount exceeds maximum threshold
- **TC-0022** [BOUNDARY] Boundary: transaction amount below minimum threshold
- **TC-0023** [BOUNDARY] Boundary: insufficient funds — balance equals total debit amount
- **TC-0024** [BOUNDARY] Boundary: insufficient funds — balance less than total debit amount
- **TC-0025** [INTEGRATION] Integration: high-risk account detection during logging state
- **TC-0026** [INTEGRATION] Integration: timeout during credit processing (type 6 transaction)
- **TC-0027** [EQUIVALENCE] Equivalence: legacy and migrated system produce identical output for representative transaction

_EQUIVALENCE tests assert the new output matches the legacy system for identical inputs (mind numeric rounding/precision)._

**Definition of done:**

- [ ] TC-0019 passes: a valid transaction (amount within min/max, sufficient balance) processes successfully, fee is added, balance is debited, and result is returned with success status.
- [ ] TC-0020 passes: transaction amount exactly equal to WS-RULE-MIN-AMOUNT is accepted and processed without error.
- [ ] TC-0021 passes: transaction amount exactly equal to WS-RULE-MAX-TRANS is accepted; amount exceeding it by 0.01 is rejected with WS-ERR-INVALID-FORMAT and state 40.
- [ ] TC-0022 passes: transaction amount below WS-RULE-MIN-AMOUNT is rejected with WS-ERR-INVALID-FORMAT and state 40; account is not debited.
- [ ] TC-0023 passes: when account balance equals WS-TEMP-AMOUNT (transaction + 15.00 fee), the transaction is accepted and balance becomes zero.
- [ ] TC-0024 passes: when account balance is less than WS-TEMP-AMOUNT, transaction is rejected with WS-ERR-INSUFF-FUNDS and state 40; account balance is unchanged.
- [ ] TC-0025 passes: after a successful debit, all accounts are scanned; any account with balance > 90,000.00 is flagged in the high-risk report with account number and balance.
- [ ] TC-0026 passes: a type 6 transaction sets error code WS-ERR-TIMEOUT and transitions to state 40 (LOG) regardless of whether the debit succeeded; the timeout error is logged.
- [ ] TC-0027 passes: for a representative set of transactions (valid, boundary, error cases), the migrated service produces byte-for-byte identical output to the legacy system (status codes, final balances, log entries, risk flags).
- [ ] All numeric calculations use decimal arithmetic with at least 2 decimal places; no floating-point rounding errors are present.
- [ ] Database transactions are atomic: either the debit and logging both succeed, or both are rolled back; no partial updates.
- [ ] Error paths do not debit the account: WS-ERR-INVALID-FORMAT, WS-ERR-INSUFF-FUNDS, and WS-ERR-TIMEOUT all result in state 40 without balance changes.
- [ ] Type 4 authorization denial logic is either removed or gated behind a feature flag with a comment referencing BR-0016 as test code.
- [ ] Service accepts ISO 8583 formatted input (or normalized JSON equivalent) and returns a structured TransactionResult DTO.
- [ ] All copybook references (SECURITY-RULES, DATABASE, TRANS-LOG-ENGINE, ISO-BITMAP-LOGIC) are mapped to equivalent service configuration, schema, and logging; no copybook is left unmapped.
- [ ] Code review confirms that state transitions, error codes, and numeric calculations match the legacy COBOL line-by-line for all 10 main flow steps and all 5 exception paths.
- [ ] Integration tests confirm that the service correctly queries the account database, updates balances, and triggers high-risk alerts without side effects.

## 8. Constraints & gotchas

- Numeric precision: WS-TEMP-AMOUNT and DB-ACC-BAL must use fixed-point decimal arithmetic (e.g., Java BigDecimal, Python Decimal) with at least 2 decimal places; floating-point arithmetic will cause rounding drift and fail TC-0027 equivalence.
- Fee calculation order: the fixed fee (15.00) must be added to the transaction amount *before* the sufficiency check; adding it after will violate BR-0013 and cause incorrect balance updates.
- Atomic debit: the balance update must occur in a single database transaction; if the debit succeeds but logging fails, the system must not retry the debit (idempotency risk).
- State transition ordering: error status codes must be set *before* transitioning to state 40 (LOG); if state is set first, logging may use the wrong status code.
- High-risk scan timing: the scan must occur *after* the debit is committed; scanning before debit will flag accounts with pre-debit balances and produce incorrect risk reports.
- Type 6 timeout handling: the timeout error must be set even if the debit succeeded; this is a simulation rule that overrides normal success logic and must be preserved for TC-0026.
- Account index bounds: ACC-IDX must be validated against the account table size from DATABASE.CPY; out-of-bounds access will cause silent failures or array exceptions.
- ISO 8583 field mapping: WS-ISO-FLD-004-AMOUNT must be extracted from the correct byte offset and length as defined in ISO-BITMAP-LOGIC.CPY; misalignment will cause amount parsing errors.
- Insufficient funds check must not debit the account; the legacy system explicitly prevents overdrafts by checking *before* debit; reversing this order violates BR-0014.
- High-risk threshold comparison: the 90,000.00 threshold is a hard boundary; accounts with balance exactly equal to 90,000.00 must not be flagged (> not >=).
- Logging state 40 is a terminal state in the legacy system; ensure the service does not attempt further state transitions after entering state 40.

## 9. Implementation steps

1. Extract numeric rule constants from SECURITY-RULES.CPY: WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, fixed fee value (15.00), and high-risk threshold (90,000.00); store as immutable configuration in the service.
2. Define a TransactionRequest DTO that maps ISO 8583 field 004 (WS-ISO-FLD-004-AMOUNT) and transaction type (WS-TRANS-TYPE) from incoming payload; validate schema matches legacy copybook structure.
3. Implement amount validation logic: reject if WS-ISO-FLD-004-AMOUNT < WS-RULE-MIN-AMOUNT OR > WS-RULE-MAX-TRANS with error code WS-ERR-INVALID-FORMAT and state transition to 40 (LOG).
4. Implement fee calculation: compute WS-TEMP-AMOUNT = WS-ISO-FLD-004-AMOUNT + 15.00 using decimal arithmetic (not floating-point) to preserve legacy precision; this total is the debit amount.
5. Implement account balance retrieval: query the account database (from DATABASE.CPY schema) using the account index (ACC-IDX) to fetch DB-ACC-BAL; handle missing account as a retrieval error.
6. Implement sufficiency check: if DB-ACC-BAL < WS-TEMP-AMOUNT, set error code WS-ERR-INSUFF-FUNDS, transition to state 40 (LOG), and return without debiting; otherwise proceed to debit.
7. Implement atomic balance debit: subtract WS-TEMP-AMOUNT from DB-ACC-BAL(ACC-IDX) in a single database transaction; ensure no partial updates on failure.
8. Implement high-risk account scan: after successful debit, iterate through all accounts in the database; for each account where DB-ACC-BAL > 90,000.00, flag the account number and generate a high-risk alert (via REPORTS.CPY or equivalent logging).
9. Implement timeout simulation for type 6 transactions: if WS-TRANS-TYPE = 6, set error code WS-ERR-TIMEOUT and transition to state 40 (LOG) regardless of prior success or failure.
10. Implement transaction logging: use TRANS-LOG-ENGINE.CPY semantics to log the transaction with final status code, account index, amounts, and state; ensure logging occurs for all paths (success and all error states).
11. Implement response serialization: return a TransactionResult DTO containing status code, final balance, transaction ID, and state; ensure output format is byte-for-byte equivalent to legacy system for TC-0027.
12. Remove or gate behind a feature flag the type 4 authorization denial logic (BR-0016) identified as test code; document as technical debt for removal in production.
13. Wire the service endpoint to accept ISO 8583 payloads (or a normalized JSON equivalent) and route through the validation → fee calculation → balance check → debit → risk scan → logging pipeline.
14. Implement comprehensive error handling: catch database connection failures, timeout exceptions, and constraint violations; map each to the appropriate WS-ERR-* code and state 40 transition.

## 10. Out of scope (handled by sibling prompts)

- Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-message-processing
- Integrate services for 'Process a financial transaction with validation, fee calculation, and balance update'
