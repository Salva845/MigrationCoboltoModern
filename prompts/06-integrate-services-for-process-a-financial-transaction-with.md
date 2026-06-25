[← Playbook index](../README.md)
 · [Service: payment-message-processing](../services/payment-message-processing.md)

# Migration prompt — Integrate services for 'Process a financial transaction with validation, fee calculation, and balance update'

> You are migrating one functional slice of a legacy system to its modern replacement. Implement **only** what this prompt covers; preserve behavior exactly. Sibling slices are handled by other prompts (see *Out of scope*).

## 1. Objective

Implement a transaction processing endpoint in payment-message-processing that receives ISO 8583 formatted transactions, validates amounts against policy thresholds (WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS), calculates a fixed 15.00 fee, checks account balance sufficiency, debits the account, detects high-risk balances (>90,000.00), and returns a structured result with status codes (WS-ERR-INVALID-FORMAT, WS-ERR-INSUFF-FUNDS, WS-ERR-TIMEOUT) and state transitions (state 40 for logging). The endpoint must preserve all nine exception paths and enforce the five business rules (BR-0011 through BR-0015, BR-0017, BR-0018) extracted from BANK74.CBL and its copybooks.

## 2. Target

- **Service:** payment-message-processing
- **Bounded context:** ISO 8583 payment message parsing, validation, and transformation for financial transaction interchange
- **Tech stack:** (use the project default)
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

- `sources/banamex-cobol/VALIDATION.CPY` — **VALIDATION** (cobol-copybook)
- `sources/banamex-cobol/CUSTOMER.CPY` — **CUSTOMER** (cobol-copybook)
- `sources/banamex-cobol/COREVARS.CPY` — **COREVARS** (cobol-copybook)
- `sources/banamex-cobol/ACCOUNT.CPY` — **ACCOUNT** (cobol-copybook)
- `sources/banamex-cobol/ISO8583.CPY` — **ISO8583** (cobol-copybook)
- `sources/banamex-cobol/CONSTANTS.CPY` — **CONSTANTS** (cobol-copybook)
- `sources/banamex-cobol/MESSAGES.CPY` — **MESSAGES** (cobol-copybook)
- `sources/banamex-cobol/ISO-PARSER.CPY` — **ISO-PARSER** (cobol-copybook)
- `sources/banamex-cobol/BANK74.CBL` — **BANK74** (cobol)

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

- [ ] ✓ All five business rules (BR-0011, BR-0012, BR-0013, BR-0014, BR-0015) are implemented and unit-tested in isolation.
- [ ] ✓ BR-0017 (timeout simulation for type 6) and BR-0018 (high-risk account detection) are implemented and tested.
- [ ] ✓ BR-0016 (type 4 authorization denial) is either preserved with a feature flag or explicitly removed with documented justification.
- [ ] ✓ The endpoint accepts ISO 8583 formatted input and correctly parses WS-ISO-FLD-004-AMOUNT and WS-TRANS-TYPE.
- [ ] ✓ Fee calculation (15.00 fixed fee) uses fixed-point decimal arithmetic with exactly 2 decimal places; no floating-point rounding errors.
- [ ] ✓ Validation chain executes in order: min threshold → max threshold → fee calculation → balance check → debit → high-risk scan → logging.
- [ ] ✓ All nine exception paths are implemented: (1) amount < min, (2) amount > max, (3) insufficient funds, (4) type 6 timeout, (5) type 4 rejection (if preserved), (6) success path, plus state transitions to state 40 (LOG) for errors.
- [ ] ✓ Account debit is atomic: balance is only updated if all validations pass and funds are sufficient. No partial debits on validation failure.
- [ ] ✓ High-risk account scan executes during state 40 (logging) for all transactions, regardless of success/failure. Alerts are logged with account numbers.
- [ ] ✓ State machine transitions are explicit: state 40 is terminal for all error paths; state 30 (CREDIT) is only reached on success.
- [ ] ✓ Response schema includes: status code, final balance, high-risk flag(s), state, and transaction ID. Numeric fields use 2 decimal places.
- [ ] ✓ TC-0019 (happy path) passes: valid transaction processes, fee is calculated, balance is updated, result is returned.
- [ ] ✓ TC-0020 (boundary: amount = min) passes: transaction at minimum threshold is accepted.
- [ ] ✓ TC-0021 (boundary: amount > max) passes: transaction exceeding maximum is rejected with WS-ERR-INVALID-FORMAT, state 40.
- [ ] ✓ TC-0022 (boundary: amount < min) passes: transaction below minimum is rejected with WS-ERR-INVALID-FORMAT, state 40.
- [ ] ✓ TC-0023 (boundary: balance = total debit) passes: account with balance exactly equal to transaction + fee is debited successfully.
- [ ] ✓ TC-0024 (boundary: balance < total debit) passes: insufficient funds is detected, WS-ERR-INSUFF-FUNDS is set, state 40, account is NOT debited.
- [ ] ✓ TC-0025 (integration: high-risk detection) passes: accounts with balance > 90,000.00 are flagged during logging state.
- [ ] ✓ TC-0026 (integration: type 6 timeout) passes: type 6 transactions set WS-ERR-TIMEOUT and transition to state 40 without credit processing.
- [ ] ✓ TC-0027 (equivalence: legacy vs. migrated) passes: output from migrated service matches legacy BANK74 output byte-for-byte for representative transactions (including status codes, final balances, high-risk flags, and log entries).
- [ ] ✓ All copybook field names are mapped to target service domain model fields; mapping is documented.
- [ ] ✓ Error codes (WS-ERR-INVALID-FORMAT, WS-ERR-INSUFF-FUNDS, WS-ERR-TIMEOUT) are defined and returned in responses.
- [ ] ✓ Code review confirms no floating-point arithmetic is used for currency; all amounts are fixed-point decimal.
- [ ] ✓ Integration tests confirm account balance retrieval and debit operations are persisted correctly.
- [ ] ✓ Logging output includes transaction ID, original amount, fee, final balance, status code, and high-risk alerts; format matches legacy system expectations.
- [ ] ✓ Feature flag or removal documentation exists for BR-0016 (type 4 rejection).
- [ ] ✓ All acceptance tests pass in the target environment (payment-message-processing service).

## 8. Constraints & gotchas

- NUMERIC PRECISION: Currency amounts must use fixed-point arithmetic with exactly 2 decimal places. The legacy system uses COBOL COMP-3 (packed decimal); ensure the target service uses a decimal type (e.g., Java BigDecimal, Python Decimal) that preserves trailing zeros and prevents floating-point rounding errors. Test boundary cases: 0.01, 99999.99, and amounts that sum to values like 10015.00 (transaction 10000.00 + fee 15.00).
- FEE CALCULATION ORDER: BR-0013 specifies fee is added to the transaction amount *before* balance sufficiency check. Do not defer fee calculation or apply it post-debit. The total debit amount (transaction + 15.00) must be compared against available balance.
- INSUFFICIENT FUNDS ATOMICITY: If balance check fails, the account must NOT be debited. Ensure the debit operation (step 7) is only executed after the balance sufficiency check passes. If using a database transaction, roll back on insufficient funds.
- HIGH-RISK SCAN TIMING: BR-0018 requires scanning all accounts during state 40 (logging), not just the account being debited. This scan must execute even if the transaction failed validation or had insufficient funds. Do not skip the scan on error paths.
- STATE MACHINE SEMANTICS: State 40 is the logging/terminal state for all error paths. State 30 (CREDIT) is only reached if all validations pass and funds are sufficient. Type 6 transactions bypass credit processing and go directly to state 40. Ensure state transitions are explicit and match the legacy state diagram.
- TIMEOUT SIMULATION (TYPE 6): This is a test/simulation rule (BR-0017). If WS-TRANS-TYPE = 6, set WS-ERR-TIMEOUT unconditionally and transition to state 40. Do not attempt to process the transaction as a normal credit. This rule must be preserved for test compatibility but may be removed in a future production-only build.
- AUTHORIZATION DENIAL (TYPE 4): BR-0016 identifies type 4 rejection as test code. The legacy system rejects type 4 transactions. During migration, either (a) preserve this behavior for backward compatibility with existing test suites, or (b) gate it behind a feature flag and document the removal. Do not silently drop this logic without explicit decision.
- ACCOUNT INDEXING: The legacy code uses ACC-IDX and WS-IDX-1 to index into account arrays (DB-ACC-BAL). Ensure the target service correctly maps account identifiers to array indices or uses a keyed lookup (e.g., account number → balance). Verify that the high-risk scan iterates over all accounts, not just the one being debited.
- ISO 8583 PARSING: The inbound message is ISO 8583 format (inferred from copybook references). Ensure the parser correctly extracts WS-ISO-FLD-004-AMOUNT and WS-TRANS-TYPE. If the legacy parser is in ISO-PARSER.CPY, extract its logic or replace it with a standard ISO 8583 library. Validate that field 4 (amount) is parsed as a numeric string and converted to decimal with 2 decimal places.
- ERROR STATUS CODE MAPPING: The legacy system uses symbolic error codes (WS-ERR-INVALID-FORMAT, WS-ERR-INSUFF-FUNDS, WS-ERR-TIMEOUT). Define the numeric or string representation of these codes in the target service and ensure they are returned in the response. Do not invent new error codes; use only those defined in CONSTANTS.CPY or MESSAGES.CPY.
- IDEMPOTENCY: If the same transaction is processed twice (e.g., due to a retry), the account balance will be debited twice. The legacy system does not appear to have idempotency guards. Clarify with the product owner whether duplicate transactions should be rejected or allowed. If idempotency is required, implement a transaction ID deduplication mechanism.
- LOGGING AND COMPLIANCE: The high-risk account detection (BR-0018) is a compliance requirement. Ensure high-risk alerts are logged and persisted for audit trails. Do not suppress or filter these alerts based on transaction success/failure.
- COPYBOOK FIELD NAMES: The implementation references field names from copybooks (WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, WS-TEMP-AMOUNT, DB-ACC-BAL, etc.). These are COBOL variable names and may not exist in the target service. Map them to equivalent domain model fields (e.g., WS-RULE-MIN-AMOUNT → minTransactionAmount, DB-ACC-BAL → accountBalance). Document the mapping to ensure consistency across the codebase.

## 9. Implementation steps

1. 1. Extract and document the numeric constants from CONSTANTS.CPY: WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, fixed fee (15.00), high-risk threshold (90,000.00), and all error status codes (WS-ERR-INVALID-FORMAT, WS-ERR-INSUFF-FUNDS, WS-ERR-TIMEOUT). Verify decimal precision (inferred as 2 decimal places for currency).
2. 2. Parse the ISO 8583 copybook (ISO8583.CPY) to identify the field mapping for transaction amount (inferred as WS-ISO-FLD-004-AMOUNT) and transaction type (WS-TRANS-TYPE). Create a schema definition for the inbound message contract.
3. 3. Design the account balance retrieval interface: define the query/RPC to fetch DB-ACC-BAL indexed by account identifier (ACC-IDX). Confirm the data type and precision of balance fields to prevent rounding drift.
4. 4. Implement the validation chain as a sequence of guards (not nested if-else) to match legacy state machine semantics: (a) amount < WS-RULE-MIN-AMOUNT → WS-ERR-INVALID-FORMAT, state 40; (b) amount > WS-RULE-MAX-TRANS → WS-ERR-INVALID-FORMAT, state 40; (c) amount >= WS-RULE-MIN-AMOUNT AND amount <= WS-RULE-MAX-TRANS → proceed to fee calculation.
5. 5. Implement fee calculation as: WS-TEMP-AMOUNT = WS-ISO-FLD-004-AMOUNT + 15.00. Use fixed-point arithmetic (not floating-point) to preserve exact 2-decimal-place currency semantics.
6. 6. Implement balance sufficiency check: if DB-ACC-BAL < WS-TEMP-AMOUNT, set WS-ERR-INSUFF-FUNDS, state 40, and do NOT debit. Otherwise, proceed to debit.
7. 7. Implement account debit as an atomic operation: DB-ACC-BAL(ACC-IDX) = DB-ACC-BAL(ACC-IDX) - WS-TEMP-AMOUNT. Ensure this is persisted to the account database before proceeding.
8. 8. Implement high-risk account detection as a separate scan loop (not conditional on transaction success): iterate all accounts, check if DB-ACC-BAL(WS-IDX-1) > 90,000.00, and emit a high-risk alert with account number for each match. This must execute during state 40 (logging) regardless of transaction outcome.
9. 9. Implement timeout simulation for transaction type 6: if WS-TRANS-TYPE = 6, set WS-ERR-TIMEOUT and transition to state 40. Do not attempt credit processing for type 6.
10. 10. Remove or gate behind a feature flag the type 4 authorization denial logic (BR-0016) identified as test code unsuitable for production. Document this removal in the migration notes.
11. 11. Implement state machine transitions: state 40 (LOG) is the terminal state for all error paths and for type 6 transactions. Success path (no validation errors, sufficient funds, type ≠ 6) transitions to state 30 (CREDIT) before logging.
12. 12. Implement structured logging at state 40: log transaction ID, original amount, fee, final balance, status code, and high-risk flags. Ensure log output matches the format expected by downstream compliance systems.
13. 13. Define the response schema: return transaction result with status code, final balance, high-risk flag, and state. Ensure numeric fields use the same precision as the legacy system (inferred 2 decimal places).
14. 14. Create unit tests for each validation rule (BR-0011, BR-0012, BR-0013, BR-0014, BR-0015, BR-0017, BR-0018) in isolation, then integration tests for the full flow.
15. 15. Run acceptance tests TC-0019 through TC-0027 against the implemented endpoint. Capture actual vs. expected output for TC-0027 (equivalence test) and verify byte-for-byte match with legacy BANK74 output for representative transactions.

## 10. Out of scope (handled by sibling prompts)

- Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-message-processing
- Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-processing-core
