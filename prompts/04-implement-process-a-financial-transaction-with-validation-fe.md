[← Playbook index](../README.md)
 · [Service: payment-message-processing](../services/payment-message-processing.md)

# Migration prompt — Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-message-processing

> You are migrating one functional slice of a legacy system to its modern replacement. Implement **only** what this prompt covers; preserve behavior exactly. Sibling slices are handled by other prompts (see *Out of scope*).

## 1. Objective

Implement a transaction processing module in payment-message-processing that receives ISO 8583 formatted transactions, validates amounts against policy thresholds (WS-RULE-MIN-AMOUNT and WS-RULE-MAX-TRANS), calculates a total debit by adding a fixed 15.00 fee to the transaction amount, checks account balance sufficiency, debits the account if funds are available, detects high-risk accounts with balances exceeding 90,000.00, and returns a transaction result with appropriate status codes and state transitions. The module must preserve all nine exception paths and enforce the five core business rules (BR-0011, BR-0012, BR-0013, BR-0014, BR-0015, BR-0018) while removing test-only authorization denial logic (BR-0016).

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

- [ ] TC-0019 passes: A valid transaction with amount between min and max thresholds, sufficient account balance, and non-timeout type processes successfully; the account balance is debited by (transaction amount + 15.00), and the result includes status code indicating success and state 30 (CREDIT).
- [ ] TC-0020 passes: A transaction with amount exactly equal to WS-RULE-MIN-AMOUNT is accepted and processed; no error is raised for being at the boundary.
- [ ] TC-0021 passes: A transaction with amount exceeding WS-RULE-MAX-TRANS is rejected with error status WS-ERR-INVALID-FORMAT and state 40 (LOG); the account balance is not modified.
- [ ] TC-0022 passes: A transaction with amount below WS-RULE-MIN-AMOUNT is rejected with error status WS-ERR-INVALID-FORMAT and state 40 (LOG); the account balance is not modified.
- [ ] TC-0023 passes: A transaction where the account balance equals the total debit amount (transaction + 15.00 fee) is accepted; the balance is reduced to zero and the transaction succeeds.
- [ ] TC-0024 passes: A transaction where the account balance is less than the total debit amount is rejected with error status WS-ERR-INSUFF-FUNDS and state 40 (LOG); the account balance is not modified.
- [ ] TC-0025 passes: During the logging state (state 40), all accounts are scanned; any account with balance > 90,000.00 is flagged and included in the transaction log output.
- [ ] TC-0026 passes: A transaction with WS-TRANS-TYPE = 6 is processed, the credit state is entered, but error status WS-ERR-TIMEOUT is set and state 40 (LOG) is transitioned to; the account balance is not modified.
- [ ] TC-0027 passes: A representative transaction (valid amount, sufficient funds, non-timeout type) produces identical output (status code, state, updated balance, high-risk flags) when processed by both the legacy BANK74.CBL system and the migrated payment-message-processing module.
- [ ] All five core business rules (BR-0011, BR-0012, BR-0013, BR-0014, BR-0015, BR-0018) are implemented and verified by unit tests.
- [ ] Test-only authorization denial logic (BR-0016, type 4 rejection) is removed or clearly marked as deprecated and non-functional in the migrated code.
- [ ] Code review confirms that all nine exception paths are implemented: min threshold, max threshold, insufficient funds, timeout (type 6), and five others as identified in the legacy source.
- [ ] Integration test confirms that the module correctly reads from and writes to the account balance database, and that concurrent transactions do not cause race conditions or double-debits.
- [ ] Numeric precision test confirms that all monetary calculations use fixed-point decimal arithmetic and preserve exactly 2 decimal places; no floating-point rounding errors are present.
- [ ] Documentation is updated to describe the TransactionRequest and TransactionResult DTOs, the state machine (state 30 for success, state 40 for logging), and the high-risk account detection scan.
- [ ] All acceptance tests (TC-0019 through TC-0027) pass in the target environment.

## 8. Constraints & gotchas

- Numeric precision: The fixed fee is 15.00 and the high-risk threshold is 90,000.00. Use fixed-point decimal arithmetic (not floating-point) to avoid rounding errors during addition and comparison. Ensure all balance calculations preserve exactly 2 decimal places.
- Boundary condition ordering: Validate minimum threshold BEFORE maximum threshold; both must fail with WS-ERR-INVALID-FORMAT and state 40. Do not proceed to fee calculation if either boundary check fails.
- Fee calculation timing: The 15.00 fee must be added to the transaction amount BEFORE the balance sufficiency check. The total debit (transaction + 15.00) is what is compared against the account balance and what is subtracted from the balance.
- Insufficient funds atomicity: If CheckSufficientFunds fails, the account balance must NOT be modified. Ensure the UpdateAccountBalance function is only called after a successful sufficiency check.
- High-risk detection scope: The high-risk account scan must iterate through ALL accounts in the database, not just the account being debited. This is a compliance scan that runs during state 40 (LOG) regardless of transaction outcome.
- Timeout simulation: Type 6 transactions must set WS-ERR-TIMEOUT and transition to state 40 even if the credit processing would have succeeded. This is a test simulation rule; ensure it is clearly marked as such and removable.
- State machine enforcement: All error paths must transition to state 40 (LOG). Only successful transactions transition to state 30 (CREDIT). The logging state must execute HighRiskAccountDetection before returning the result.
- ISO 8583 parsing: The transaction amount is in field 004. Ensure the parser correctly extracts this field and handles variable-length encoding if present in the copybook definition.
- Database consistency: Account balance updates must be persisted atomically. If the database connection fails during UpdateAccountBalance, the transaction must be rolled back and an error status returned.
- Idempotency: If the same transaction is processed twice (e.g., due to a retry), the second processing must not double-debit the account. Consider implementing a transaction ID check or idempotency key if the legacy system does not already enforce this.
- Logging state 40 execution order: HighRiskAccountDetection must run during state 40 (LOG) after the transaction outcome is determined, so that high-risk flags are included in the final transaction log.

## 9. Implementation steps

1. Extract and document the numeric constants from CONSTANTS.CPY: WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, fixed fee (15.00), high-risk threshold (90,000.00), and all error status codes (WS-ERR-INVALID-FORMAT, WS-ERR-INSUFF-FUNDS, WS-ERR-TIMEOUT).
2. Create a TransactionRequest DTO that maps ISO 8583 field 004 (transaction amount) and transaction type from the incoming message; parse using ISO-PARSER.CPY logic or equivalent.
3. Implement a ValidateTransactionAmount function that checks if the parsed amount is >= WS-RULE-MIN-AMOUNT and <= WS-RULE-MAX-TRANS; return error status WS-ERR-INVALID-FORMAT and state 40 (LOG) if either boundary is violated (BR-0011, BR-0012).
4. Implement a CalculateTotalDebit function that adds 15.00 to the transaction amount and stores the result; this total is used for all subsequent balance checks and debit operations (BR-0013).
5. Implement an AccountBalanceLookup function that retrieves the current balance from the database using the account identifier; handle database connection failures and timeouts explicitly.
6. Implement a CheckSufficientFunds function that compares the account balance against the total debit amount (transaction + 15.00 fee); if balance < total debit, set error status WS-ERR-INSUFF-FUNDS, transition to state 40 (LOG), and do NOT debit the account (BR-0014).
7. Implement an UpdateAccountBalance function that subtracts the total debit amount from the account balance and persists the change to the database; this operation must be atomic and must only execute if CheckSufficientFunds passed (BR-0015).
8. Implement a HighRiskAccountDetection function that iterates through all accounts in the database, identifies any with balance > 90,000.00, and logs/flags each for compliance reporting; this must execute during the logging state (state 40) regardless of transaction success or failure (BR-0018).
9. Implement timeout simulation logic: if WS-TRANS-TYPE = 6, set error status WS-ERR-TIMEOUT and transition to state 40 (LOG) after attempting credit processing (BR-0017).
10. Remove or stub out the type 4 authorization denial logic (BR-0016) as it is identified as test code unsuitable for production.
11. Implement a TransactionLogger that records the transaction with its final status code and state; ensure logging occurs for both success (state 30) and failure (state 40) paths.
12. Create a TransactionResult DTO that returns the final status code, state, updated account balance (if debited), and any high-risk flags to the caller.
13. Wire the module into the payment-message-processing service's request handler so that incoming ISO 8583 messages are routed to ValidateTransactionAmount → CalculateTotalDebit → AccountBalanceLookup → CheckSufficientFunds → UpdateAccountBalance → HighRiskAccountDetection → TransactionLogger → TransactionResult.
14. Implement comprehensive error handling for each step: database unavailability, network timeouts, malformed ISO 8583 input, and concurrent account access; ensure all error paths transition to state 40 (LOG).
15. Add numeric precision handling: ensure all monetary amounts (transaction amount, fee, balance) are stored and compared using a consistent decimal type (e.g., BigDecimal in Java, Decimal in .NET) with at least 2 decimal places; avoid floating-point arithmetic.

## 10. Out of scope (handled by sibling prompts)

- Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-processing-core
- Integrate services for 'Process a financial transaction with validation, fee calculation, and balance update'
