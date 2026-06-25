[← Playbook index](../README.md)
 · [Service: payment-message-processing](../services/payment-message-processing.md)

# Migration prompt — Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-message-processing

> You are migrating one functional slice of a legacy system to its modern replacement. Implement **only** what this prompt covers; preserve behavior exactly. Sibling slices are handled by other prompts (see *Out of scope*).

## 1. Objective

Implement the core transaction validation and processing pipeline in payment-message-processing service. This pipeline receives ISO 8583 financial transaction messages, extracts amount and type fields, retrieves customer and account details from the database, applies 10 sequential business rules (amount thresholds, blacklist checks, account type restrictions, fraud risk scoring, balance verification, fee and VAT calculation), debits the account atomically, transitions the transaction through state machine (validation → debit → credit or audit), logs to transaction log engine, and returns success or error status with appropriate error codes (WS-ERR-INVALID-FORMAT, WS-ERR-BLACKLISTED, WS-ERR-AUTH-DENIED, WS-ERR-INSUFF-FUNDS). The service must preserve all exception paths and state transitions from the legacy BANK85 orchestrator.

## 2. Target

- **Service:** payment-message-processing
- **Bounded context:** ISO 8583 payment message parsing, validation, and transformation for financial transaction interchange
- **Tech stack:** (use the project default)
- **Shared data (coordinate ownership):** ACCOUNT, CONSTANTS, COREVARS, CUSTOMER, DATABASE, DATETIME, ISO-BITMAP-LOGIC, ISO-PARSER, ISO8583, MESSAGES, REPORT-TEMPLATES, REPORTS, SECURITY-RULES, SYSTEM-SPECS, TRANS-LOG-ENGINE, VALIDATION
- **Migration wave:** 1

## 3. Functionality to preserve

BANK85 orchestrates the core transaction processing workflow for a banking system, applying validation rules (amount thresholds, account eligibility), compliance controls (blacklist checks, fraud risk scoring), and pricing/tax calculations. The system evaluates transaction feasibility, applies fees and taxes, and routes transactions through audit or credit states based on risk and regulatory requirements.

**Main flow (MUST preserve):**

1. Parse incoming transaction message and extract ISO 8583 fields (amount, transaction type, account identifier)
2. Retrieve customer and account details from database
3. Validate transaction amount against minimum and maximum thresholds; reject if out of bounds
4. Check account status for blacklist flag ('B'); reject if blacklisted and route to audit state
5. Verify account type eligibility (block SPEI transfers from payroll accounts)
6. Calculate fraud risk score based on transaction amount and account risk level; block if score exceeds 60
7. Verify sufficient account balance to cover transaction amount; reject if insufficient funds
8. Calculate SPEI fee (12.50 units if transaction type = 2, else 0)
9. Calculate VAT/IVA tax on amount plus fees
10. Compute final total amount to be debited (original amount + fee + VAT)
11. Debit account balance by final total amount
12. Transition transaction to credit state (30) for posting
13. Log transaction details to transaction log engine
14. Return success status to caller

**Exception handling (MUST preserve):**

- Amount below minimum threshold: display warning, set error code WS-ERR-INVALID-FORMAT, reject transaction
- Amount exceeds maximum threshold: display warning, set error code WS-ERR-INVALID-FORMAT, reject transaction
- Account is blacklisted (status 'B'): set error code WS-ERR-BLACKLISTED, transition to audit state (40)
- SPEI transfer from payroll account: display restriction message, set error code WS-ERR-AUTH-DENIED, reject transaction
- Insufficient funds: set error code WS-ERR-INSUFF-FUNDS, transition to audit state (40), do not debit account
- Fraud risk score exceeds 60: set error code WS-ERR-BLACKLISTED, display fraud alert with score, block transaction
- Employee ID is even (payroll eligibility): set employee status to 'P' (blocked), display block message, reject payroll transaction

**Business rules (MUST preserve exactly):**

- **BR-0001 Account Status Blacklist Check** — Reject transactions if the account is marked with status 'B' (blacklisted). This is a compliance/risk control to prevent transactions on flagged accounts.
  - Logic: Check if DB-ACC-ST at the found account index equals 'B'. If true, set status code to WS-ERR-BLACKLISTED and transition to audit state (40).
  - Triggers when: During validation state (3200-STATE-VALIDATE), after account is found in database
  - Exceptions: None explicitly handled; blacklist status immediately blocks transaction
- **BR-0002 Minimum Transaction Amount Validation** — Enforce a minimum transaction amount threshold. Transactions below WS-RULE-MIN-AMOUNT are rejected as invalid.
  - Logic: Compare WS-ISO-FLD-004-AMOUNT against WS-RULE-MIN-AMOUNT. If amount is less than minimum, display warning and set status code to WS-ERR-INVALID-FORMAT.
  - Triggers when: During business rules engine execution (4800-BUSINESS-RULES-ENGINE) in validation state
  - Exceptions: None; rule applies uniformly to all transaction types
- **BR-0003 Maximum Transaction Amount Validation** — Enforce a maximum transaction amount limit per transaction. Transactions exceeding WS-RULE-MAX-TRANS are rejected.
  - Logic: Compare WS-ISO-FLD-004-AMOUNT against WS-RULE-MAX-TRANS. If amount exceeds maximum, display warning and set status code to WS-ERR-INVALID-FORMAT.
  - Triggers when: During business rules engine execution (4800-BUSINESS-RULES-ENGINE) in validation state
  - Exceptions: None; rule applies uniformly to all transaction types
- **BR-0004 Account Type Restriction for SPEI Transfers** — Prevent SPEI (interbank) transfers from payroll accounts. Payroll accounts (WS-ACC-TYPE = 'NOMINA') are restricted from SPEI transactions (WS-TRANS-TYPE = 2).
  - Logic: Check if WS-ACC-TYPE equals 'NOMINA' AND WS-TRANS-TYPE equals 2. If both conditions are true, display restriction message and set status code to WS-ERR-AUTH-DENIED.
  - Triggers when: During business rules engine execution (4800-BUSINESS-RULES-ENGINE) when account type is NOMINA and transaction type is SPEI (2)
  - Exceptions: Restriction applies only to SPEI transfers; other transaction types from payroll accounts are allowed
- **BR-0005 Insufficient Funds Check** — Prevent debit transactions if account balance is insufficient to cover the transaction amount. This is a core financial control.
  - Logic: Compare DB-ACC-BAL at the account index against WS-TEMP-AMOUNT. If balance is less than amount, set status code to WS-ERR-INSUFF-FUNDS and transition to audit state (40). Otherwise, subtract amount from balance and proceed to credit state (30).
  - Triggers when: During debit state (3400-STATE-DEBIT) after financial calculations are complete
  - Exceptions: Transaction is blocked entirely if funds are insufficient; no partial debit is allowed
- **BR-0006 SPEI Fee Calculation** — Apply a fixed fee of 12.50 currency units for SPEI (interbank) transfers. This is a pricing rule that implements the bank's fee schedule.
  - Logic: Check if WS-TRANS-TYPE equals 2. If true, set WS-FEE-AMOUNT to 12.50 and display confirmation. Otherwise, set WS-FEE-AMOUNT to 0.
  - Triggers when: During financial calculations (4500-CALCULATE-FINANCIALS) when transaction type is SPEI (2)
  - Exceptions: Fee is only applied to SPEI transfers; other transaction types incur no fee
- **BR-0007 VAT and Total Amount Calculation** — Calculate and apply VAT (IVA) tax on the transaction amount plus fees, then compute the final total amount to be debited. This implements the tax calculation policy.
  - Logic: Compute WS-VAT-AMOUNT as (WS-TEMP-AMOUNT + WS-FEE-AMOUNT) multiplied by WS-TAX-IVA. Then compute final WS-TEMP-AMOUNT as original amount plus VAT plus fee.
  - Triggers when: During financial calculations (4500-CALCULATE-FINANCIALS) after fee determination
  - Exceptions: VAT is calculated on the sum of base amount and fees; no exemptions are coded
- **BR-0008 Fraud Risk Scoring and Blocking** — Calculate a fraud risk score based on transaction amount and account risk level. Block transactions if score exceeds 60 points. This is a compliance/anti-fraud control.
  - Logic: Initialize WS-FRAUD-RISK-SCORE to 0. Add 40 points if WS-TEMP-AMOUNT exceeds 50,000. Add 30 points if DB-ACC-RISK at account index exceeds 2. If final score exceeds 60, set status code to WS-ERR-BLACKLISTED and display fraud alert with score.
  - Triggers when: During fraud heuristics evaluation (8500-FRAUD-HEURISTICS) in validation state
  - Exceptions: Score thresholds are fixed (40 for high amount, 30 for high account risk, 60 for block); no graduated response
- **BR-0009 Employee Validation for Payroll Processing** — Block payroll payments for employees with even-numbered IDs. This appears to be a test/simulation rule for payroll eligibility.
  - Logic: Check if PR-EMP-ID modulo 2 equals 0 (even ID). If true, set PR-EMP-STATUS to 'P' (blocked) and display block message. Otherwise, status remains 'V' (valid).
  - Triggers when: During employee validation (5200-VALIDATE-EMPLOYEE) in bulk payroll processing
  - Exceptions: Only even-numbered employee IDs are blocked; odd IDs proceed normally
- **BR-0010 High Balance Risk Flagging** — Identify accounts with balances exceeding 80,000 as high-risk for portfolio analysis. This is a risk monitoring rule used in settlement reporting.
  - Logic: Iterate through 100 accounts. For each account, check if DB-ACC-BAL exceeds 80,000. If true, display high-risk alert with account number and balance.
  - Triggers when: During risk report generation (6500-GENERATE-RISK-REPORT) in settlement report flow
  - Exceptions: Flagging is informational only; no transaction blocking occurs at this stage

## 4. Source references (legacy)

- `sources/banamex-cobol/VALIDATION.CPY` — **VALIDATION** (cobol-copybook)
- `sources/banamex-cobol/CUSTOMER.CPY` — **CUSTOMER** (cobol-copybook)
- `sources/banamex-cobol/COREVARS.CPY` — **COREVARS** (cobol-copybook)
- `sources/banamex-cobol/ACCOUNT.CPY` — **ACCOUNT** (cobol-copybook)
- `sources/banamex-cobol/ISO8583.CPY` — **ISO8583** (cobol-copybook)
- `sources/banamex-cobol/CONSTANTS.CPY` — **CONSTANTS** (cobol-copybook)
- `sources/banamex-cobol/MESSAGES.CPY` — **MESSAGES** (cobol-copybook)
- `sources/banamex-cobol/ISO-PARSER.CPY` — **ISO-PARSER** (cobol-copybook)

_See the DeepWiki bundle's `modules/` pages for full documentation of each._

## 5. Input / output contract

_No structured I/O captured; infer from source + business rules._

## 6. Dependencies & integration

_No cross-service dependencies detected for this slice._

## 7. Acceptance criteria

The implementation must pass these test cases:

- **TC-0001** [FUNCTIONAL] Happy path: valid transaction processes end-to-end with fees, VAT, and credit state transition
- **TC-0002** [BOUNDARY] Boundary: transaction amount exactly at minimum threshold passes validation
- **TC-0003** [BOUNDARY] Boundary: transaction amount one unit below minimum threshold rejected
- **TC-0004** [BOUNDARY] Boundary: transaction amount exactly at maximum threshold passes validation
- **TC-0005** [BOUNDARY] Boundary: transaction amount one unit above maximum threshold rejected
- **TC-0006** [BOUNDARY] Boundary: fraud risk score exactly at threshold (60) blocks transaction
- **TC-0007** [BOUNDARY] Boundary: fraud risk score just below threshold (59) allows transaction
- **TC-0008** [BOUNDARY] Boundary: SPEI fee calculation for transaction type 2 vs. non-SPEI
- **TC-0009** [BOUNDARY] Boundary: account balance exactly sufficient for transaction (no margin)
- **TC-0010** [BOUNDARY] Boundary: account balance one unit below final debit amount triggers insufficient funds
- **TC-0011** [FUNCTIONAL] Blacklist check: account with status 'B' transitions to audit state and rejects
- **TC-0012** [FUNCTIONAL] Account type restriction: SPEI transfer from NOMINA account rejected
- **TC-0013** [FUNCTIONAL] Account type restriction: non-SPEI transfer from NOMINA account allowed
- **TC-0014** [EQUIVALENCE] VAT and total amount calculation: verify formula (amount + fee) × tax rate + original amount
- **TC-0015** [INTEGRATION] Integration: transaction validation state (3200) to debit state (3400) to credit state (30) transition
- **TC-0016** [INTEGRATION] Integration: audit state (40) transition on blacklist or insufficient funds
- **TC-0017** [INTEGRATION] Integration: database account retrieval and balance update atomicity
- **TC-0018** [INTEGRATION] Integration: transaction log engine receives complete transaction details

_EQUIVALENCE tests assert the new output matches the legacy system for identical inputs (mind numeric rounding/precision)._

**Definition of done:**

- [ ] ✓ TransactionValidationController endpoint created and accepts POST /transactions/validate with ISO 8583 message payload.
- [ ] ✓ ISOMessageParser service extracts all required fields (amount, transaction type, account ID, customer ID) from ISO 8583 message.
- [ ] ✓ AccountRepository.findByAccountId() retrieves account record with status, balance, risk level, and account type.
- [ ] ✓ CustomerRepository.findByCustomerId() retrieves customer record with employee ID.
- [ ] ✓ TransactionValidationService implements all 10 business rules (BR-0001 through BR-0010) in correct order with correct comparison operators and status codes.
- [ ] ✓ BR-0001 (Blacklist Check): account with status 'B' sets WS-ERR-BLACKLISTED, transitions to state 40, rejects transaction.
- [ ] ✓ BR-0002 (Minimum Amount): amount < minimum sets WS-ERR-INVALID-FORMAT, logs warning, rejects transaction.
- [ ] ✓ BR-0003 (Maximum Amount): amount > maximum sets WS-ERR-INVALID-FORMAT, logs warning, rejects transaction.
- [ ] ✓ BR-0004 (SPEI Payroll Restriction): NOMINA account + transaction type 2 sets WS-ERR-AUTH-DENIED, logs restriction message, rejects transaction.
- [ ] ✓ BR-0006 (SPEI Fee): transaction type 2 sets fee to 12.50; other types set fee to 0.
- [ ] ✓ BR-0007 (VAT Calculation): VAT = (amount + fee) × tax_rate; final_amount = original_amount + VAT + fee. Uses BigDecimal with HALF_UP rounding.
- [ ] ✓ BR-0008 (Fraud Risk Scoring): score = 0 + (amount > 50,000 ? 40 : 0) + (account_risk > 2 ? 30 : 0). If score > 60, sets WS-ERR-BLACKLISTED, logs fraud alert with score, rejects transaction.
- [ ] ✓ BR-0005 (Insufficient Funds): if balance < final_amount, sets WS-ERR-INSUFF-FUNDS, transitions to state 40, does NOT debit account.
- [ ] ✓ BR-0009 (Employee Validation): if employee_id % 2 == 0, sets employee status to 'P' (blocked), logs block message.
- [ ] ✓ BR-0010 (High Balance Flagging): iterates through 100 accounts, logs alert for each account with balance > 80,000. Does not block transaction.
- [ ] ✓ AccountDebitService.debitAccount() atomically subtracts final_amount from account balance within database transaction.
- [ ] ✓ TransactionStateTransitionService manages state machine: 3200 → 3400 → 30 on success; 3200 → 40 on rejection (blacklist or insufficient funds).
- [ ] ✓ TransactionLogService.logTransaction() writes complete transaction details (amount, fees, VAT, final debit, account ID, customer ID, status code, timestamp, state) to transaction log engine.
- [ ] ✓ TransactionResponse DTO returns success response with transaction ID, final amount, fees, VAT, new balance, state (30). Returns error response with error code, message, state (40 or rejection).
- [ ] ✓ Exception handling catches database errors, constraint violations, numeric overflow. Logs errors with context. Returns appropriate HTTP status (400, 409, 500).
- [ ] ✓ Request validation verifies ISO 8583 structure, required fields, numeric amount, valid transaction type.
- [ ] ✓ Idempotency implemented: duplicate transactions return cached response without double-debiting.
- [ ] ✓ Constants (WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, WS-TAX-IVA, fraud threshold, high balance threshold, SPEI fee, SPEI type, payroll type, blacklist status) loaded from CONSTANTS.CPY or configuration.
- [ ] ✓ TC-0001 (Happy Path): valid transaction processes end-to-end, fees calculated, VAT applied, final amount debited, state transitions to 30, transaction logged.
- [ ] ✓ TC-0002 (Boundary): amount exactly at minimum threshold passes validation.
- [ ] ✓ TC-0003 (Boundary): amount one unit below minimum threshold rejected with WS-ERR-INVALID-FORMAT.
- [ ] ✓ TC-0004 (Boundary): amount exactly at maximum threshold passes validation.
- [ ] ✓ TC-0005 (Boundary): amount one unit above maximum threshold rejected with WS-ERR-INVALID-FORMAT.
- [ ] ✓ TC-0006 (Boundary): fraud risk score exactly 60 allows transaction (not blocked).
- [ ] ✓ TC-0007 (Boundary): fraud risk score 59 allows transaction.
- [ ] ✓ TC-0008 (Boundary): SPEI fee 12.50 applied for transaction type 2; fee 0 for other types.
- [ ] ✓ TC-0009 (Boundary): account balance exactly sufficient for final debit amount (no margin) allows transaction.
- [ ] ✓ TC-0010 (Boundary): account balance one unit below final debit amount triggers WS-ERR-INSUFF-FUNDS, transitions to state 40.
- [ ] ✓ TC-0011 (Functional): account with status 'B' sets WS-ERR-BLACKLISTED, transitions to state 40, rejects transaction.
- [ ] ✓ TC-0012 (Functional): SPEI transfer (type 2) from NOMINA account rejected with WS-ERR-AUTH-DENIED.
- [ ] ✓ TC-0013 (Functional): non-SPEI transfer (type ≠ 2) from NOMINA account allowed.
- [ ] ✓ TC-0014 (Equivalence): VAT and total amount calculation verified: final_amount = original_amount + ((original_amount + fee) × tax_rate) + fee.
- [ ] ✓ TC-0015 (Integration): transaction state transitions 3200 → 3400 → 30 on success path.
- [ ] ✓ TC-0016 (Integration): transaction state transitions to 40 (audit) on blacklist or insufficient funds.
- [ ] ✓ TC-0017 (Integration): account retrieval and balance update are atomic; concurrent transactions do not cause lost updates.
- [ ] ✓ TC-0018 (Integration): transaction log engine receives complete transaction details with all fields populated.
- [ ] ✓ All acceptance tests pass (TC-0001 through TC-0018).
- [ ] ✓ Code review completed: business rules correctly implemented, no hardcoded values, proper error handling, logging at appropriate levels.
- [ ] ✓ Performance tested: single transaction processes in < 500ms (excluding network latency). Concurrent load (10+ transactions) does not degrade performance.
- [ ] ✓ Security review completed: no SQL injection, no sensitive data logged, proper authorization checks (blacklist, account type restrictions).
- [ ] ✓ Documentation updated: API contract (request/response schema), error codes, state machine diagram, business rule mapping, deployment instructions.

## 8. Constraints & gotchas

- NUMERIC PRECISION: VAT calculation (BR-0007) multiplies (amount + fee) by tax rate. Use BigDecimal with HALF_UP rounding to match legacy behavior. Test with amounts that produce non-terminating decimals (e.g., 100.00 × 0.16 = 16.00, but 100.01 × 0.16 = 16.0016 → round to 16.00 or 16.01 depending on legacy rounding rule). Legacy COBOL likely uses COMP-3 (packed decimal); verify rounding direction against CONSTANTS.CPY WS-TAX-IVA definition.
- FRAUD SCORE BOUNDARY: BR-0008 blocks if score > 60 (not ≥ 60). TC-0006 and TC-0007 test exactly 60 (allow) vs. 59 (allow). Ensure comparison operator is strictly greater-than, not greater-than-or-equal.
- AMOUNT THRESHOLD BOUNDARIES: BR-0002 and BR-0003 use < and > operators (not ≤ or ≥). TC-0002 tests amount exactly at minimum (should pass). TC-0003 tests one unit below minimum (should fail). Verify comparison operators match legacy logic.
- BALANCE DEBIT ATOMICITY: BR-0005 and debit operation must be atomic. If balance check passes but debit fails (e.g., concurrent withdrawal), transaction must not be partially applied. Use database transaction isolation level READ_COMMITTED or SERIALIZABLE. Test with concurrent requests to same account.
- STATE TRANSITION ORDERING: Transaction must transition 3200 → 3400 → 30 in sequence. If any rule fails, transition to 40 (audit) instead. Do not skip intermediate states. Verify state machine in TransactionStateTransitionService enforces valid transitions.
- EMPLOYEE ID PARITY: BR-0009 checks if PR-EMP-ID modulo 2 == 0 (even). Ensure modulo operation handles negative IDs correctly (if legacy allows negative employee IDs). Test with ID = 0 (even, should block).
- ACCOUNT BALANCE UPDATE: After debit, new balance = old balance - final_amount. Verify balance is never negative after debit (should be caught by BR-0005 check, but double-check in debit service). Do not allow balance to go negative due to rounding errors.
- SPEI FEE CONDITIONAL: BR-0006 applies 12.50 fee only if WS-TRANS-TYPE == 2. Ensure fee is exactly 12.50 (not 12.5 or 12.500). Test with transaction types 0, 1, 2, 3 to verify fee only applies to type 2.
- BLACKLIST STATE TRANSITION: BR-0001 transitions to state 40 (audit) but also rejects transaction. Ensure response indicates rejection (error code WS-ERR-BLACKLISTED) AND state is set to 40. Do not proceed to debit or credit state.
- INSUFFICIENT FUNDS STATE TRANSITION: BR-0005 transitions to state 40 (audit) but does NOT debit account. Verify account balance is NOT modified. Ensure response indicates rejection (error code WS-ERR-INSUFF-FUNDS) AND state is 40.
- FRAUD ALERT LOGGING: BR-0008 must log fraud alert with score value. Ensure score is included in log message and response. Test with score = 61 (blocks), score = 60 (allows), score = 59 (allows).
- HIGH BALANCE FLAGGING: BR-0010 iterates through 100 accounts and logs alerts. This is a side-effect monitoring rule; does not block transaction. Ensure this rule runs after all blocking rules (BR-0001 through BR-0008) so it does not interfere with transaction processing.
- EXCEPTION PATH ORDERING: Rules must be evaluated in order BR-0001 → BR-0002 → BR-0003 → BR-0004 → BR-0006 → BR-0007 → BR-0008 → BR-0005 → BR-0009 → BR-0010. If any rule rejects, stop and return error immediately. Do not evaluate subsequent rules.
- IDEMPOTENCY: If same transaction (same ISO 8583 message, same timestamp, same account) is submitted twice, return same response without double-debiting account. Use transaction ID as idempotency key. Store in transaction log with 'DUPLICATE' flag if resubmitted.
- ENCODING: ISO 8583 messages may contain non-ASCII characters (account names, customer names). Ensure UTF-8 encoding is preserved through parsing, validation, and logging. Test with special characters (accents, symbols).
- DATE/TIME HANDLING: Transaction timestamp must be captured at entry point and logged. Ensure timezone consistency (legacy likely uses local time; target service should use UTC or explicit timezone). Test with transactions near midnight and daylight saving time boundaries.
- CONCURRENT ACCOUNT ACCESS: Multiple transactions may target same account simultaneously. Database must enforce isolation to prevent lost updates. Test with 10+ concurrent transactions to same account; verify final balance is correct and no transactions are lost.
- ZERO AMOUNT EDGE CASE: If WS-ISO-FLD-004-AMOUNT == 0, should this pass minimum threshold check (BR-0002)? Legacy behavior unclear. Assume 0 is invalid (below minimum). Test with amount = 0 and verify rejection.
- NEGATIVE AMOUNT EDGE CASE: If WS-ISO-FLD-004-AMOUNT < 0, should this be rejected? Assume negative amounts are invalid. Test with amount = -100 and verify rejection (likely fails BR-0002 minimum check).
- ACCOUNT NOT FOUND: If account ID does not exist in database, AccountRepository.findByAccountId() returns null. Catch this and return error (e.g., WS-ERR-INVALID-ACCOUNT). Do not proceed to validation rules.
- CUSTOMER NOT FOUND: If customer ID does not exist, CustomerRepository.findByCustomerId() returns null. For BR-0009 (employee validation), treat as missing employee ID. Decide: reject transaction or skip employee validation. Legacy behavior unclear; assume skip if customer not found.
- TRANSACTION LOG FAILURE: If TransactionLogService.logTransaction() fails (e.g., log engine down), should transaction be rolled back? Assume logging is best-effort; do not fail transaction if log write fails. Log the logging failure separately.
- RULE CONSTANT LOADING: WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, WS-TAX-IVA must be loaded from CONSTANTS.CPY at startup or injected as configuration. Do not hardcode. Test with different constant values to verify rules use injected values, not hardcoded literals.

## 9. Implementation steps

1. 1. Create TransactionValidationController endpoint POST /transactions/validate that accepts ISO 8583 message payload (WS-ISO-FLD-004-AMOUNT, WS-TRANS-TYPE, account identifier fields from ISO8583.CPY).
2. 2. Implement ISOMessageParser service to extract and map ISO 8583 fields to domain objects: parse amount, transaction type, account ID, customer ID from incoming message using ISO-PARSER.CPY copybook structure.
3. 3. Implement AccountRepository.findByAccountId() to retrieve account record from database, populating DB-ACC-ST (status), DB-ACC-BAL (balance), DB-ACC-RISK (risk level), WS-ACC-TYPE (account type) from ACCOUNT.CPY schema.
4. 4. Implement CustomerRepository.findByCustomerId() to retrieve customer record, populating PR-EMP-ID (employee ID) from CUSTOMER.CPY schema.
5. 5. Create TransactionValidationService with sequential rule evaluation in order (BR-0001 through BR-0010). Each rule must set appropriate status code and halt further processing on rejection, or proceed to next rule on pass.
6. 6. Implement BR-0001 (Blacklist Check): if DB-ACC-ST == 'B', set WS-STATUS = WS-ERR-BLACKLISTED, transition transaction state to 40 (audit), return rejection response immediately.
7. 7. Implement BR-0002 (Minimum Amount): if WS-ISO-FLD-004-AMOUNT < WS-RULE-MIN-AMOUNT, set WS-STATUS = WS-ERR-INVALID-FORMAT, log warning message, return rejection immediately.
8. 8. Implement BR-0003 (Maximum Amount): if WS-ISO-FLD-004-AMOUNT > WS-RULE-MAX-TRANS, set WS-STATUS = WS-ERR-INVALID-FORMAT, log warning message, return rejection immediately.
9. 9. Implement BR-0004 (SPEI Payroll Restriction): if WS-ACC-TYPE == 'NOMINA' AND WS-TRANS-TYPE == 2, set WS-STATUS = WS-ERR-AUTH-DENIED, log restriction message, return rejection immediately.
10. 10. Implement BR-0006 (SPEI Fee Calculation): if WS-TRANS-TYPE == 2, set WS-FEE-AMOUNT = 12.50, else set WS-FEE-AMOUNT = 0. Store original amount in WS-TEMP-AMOUNT for subsequent calculations.
11. 11. Implement BR-0007 (VAT Calculation): compute WS-VAT-AMOUNT = (WS-TEMP-AMOUNT + WS-FEE-AMOUNT) × WS-TAX-IVA. Then compute final WS-TEMP-AMOUNT = original_amount + WS-VAT-AMOUNT + WS-FEE-AMOUNT. Store this as the final debit amount.
12. 12. Implement BR-0008 (Fraud Risk Scoring): initialize WS-FRAUD-RISK-SCORE = 0. Add 40 points if WS-TEMP-AMOUNT > 50,000. Add 30 points if DB-ACC-RISK > 2. If final score > 60, set WS-STATUS = WS-ERR-BLACKLISTED, log fraud alert with score value, return rejection immediately.
13. 13. Implement BR-0005 (Insufficient Funds Check): if DB-ACC-BAL < WS-TEMP-AMOUNT (final debit amount), set WS-STATUS = WS-ERR-INSUFF-FUNDS, transition transaction state to 40 (audit), return rejection without debiting account.
14. 14. Implement BR-0009 (Employee Validation): if PR-EMP-ID modulo 2 == 0 (even), set PR-EMP-STATUS = 'P' (blocked), log block message. This validation applies to payroll transactions; reject if blocked.
15. 15. Implement BR-0010 (High Balance Risk Flagging): iterate through all 100 accounts in database. For each account where DB-ACC-BAL > 80,000, log high-risk alert with account number and balance. This is a monitoring rule; does not block transaction.
16. 16. Implement AccountDebitService.debitAccount(accountId, finalAmount) as atomic operation: subtract WS-TEMP-AMOUNT from DB-ACC-BAL, persist updated balance to database within single transaction. If debit fails, rollback and return error.
17. 17. Implement TransactionStateTransitionService to manage state machine: transition transaction from state 3200 (validation) → 3400 (debit) → 30 (credit) on success path. On rejection, transition to state 40 (audit) for blacklist or insufficient funds cases.
18. 18. Implement TransactionLogService.logTransaction() to write complete transaction details (amount, fees, VAT, final debit, account ID, customer ID, status code, timestamp) to transaction log engine using COREVARS.CPY structure.
19. 19. Create TransactionResponse DTO mapping: return success response with transaction ID, final amount debited, fees, VAT, new account balance, and state (30). Return error response with error code, status message, and state (40 or rejection).
20. 20. Implement exception handling: catch database connection failures, constraint violations, and numeric overflow. Log errors with context (account ID, amount, rule that failed). Return appropriate HTTP status (400 for validation, 409 for conflict, 500 for system error).
21. 21. Add request validation: verify ISO 8583 message structure, required fields present, amount is numeric and non-negative, transaction type is valid integer.
22. 22. Implement idempotency: store transaction ID in request header or body. Check if transaction already processed (lookup in transaction log). If duplicate, return cached response with same transaction ID.
23. 23. Write unit tests for each business rule in isolation: mock AccountRepository, CustomerRepository, test boundary conditions (amount exactly at min/max, fraud score exactly 60/59, balance exactly sufficient).
24. 24. Write integration tests: end-to-end flow with real database (or testcontainers), verify state transitions, verify account balance updated correctly, verify transaction log entry created.
25. 25. Load test constants from CONSTANTS.CPY: WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, WS-TAX-IVA, fraud threshold (60), high balance threshold (80,000), SPEI fee (12.50), SPEI transaction type (2), payroll account type ('NOMINA'), blacklist status ('B').
26. 26. Document error code mappings: WS-ERR-INVALID-FORMAT (400), WS-ERR-BLACKLISTED (403), WS-ERR-AUTH-DENIED (403), WS-ERR-INSUFF-FUNDS (409). Return these in response body.
27. 27. Verify numeric precision: use BigDecimal for all monetary calculations (amount, fees, VAT, balance) to avoid floating-point rounding errors. Define rounding mode (HALF_UP) for VAT calculation.
28. 28. Run acceptance tests TC-0001 through TC-0018 against deployed service. Verify all state transitions, error codes, and log entries match expected behavior.

## 10. Out of scope (handled by sibling prompts)

- Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-processing-core
- Integrate services for 'Process and validate a financial transaction with compliance and fraud controls'
