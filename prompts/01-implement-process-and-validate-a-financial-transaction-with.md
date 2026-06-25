[← Playbook index](../README.md)
 · [Service: payment-processing-core](../services/payment-processing-core.md)

# Migration prompt — Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-processing-core

> You are migrating one functional slice of a legacy system to its modern replacement. Implement **only** what this prompt covers; preserve behavior exactly. Sibling slices are handled by other prompts (see *Out of scope*).

## 1. Objective

Implement the core transaction validation and processing pipeline in payment-processing-core as a stateless service handler that orchestrates ISO 8583 message parsing, multi-stage validation (amount thresholds, blacklist, account type, fraud scoring, balance sufficiency), fee and tax calculation, account debit, state transition, and audit logging. The service must preserve all 10 business rules (BR-0001 through BR-0010), enforce the exact error codes and state transitions specified, and integrate with a database abstraction layer for account retrieval and balance updates and a transaction log engine for audit trail capture.

## 2. Target

- **Service:** payment-processing-core
- **Bounded context:** ISO 8583 payment message processing, transaction logging, and security rule enforcement for financial transactions
- **Tech stack:** (use the project default)
- **Data store:** PostgreSQL
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

- `sources/banamex-cobol/BANK85.CBL` — **BANK85** (cobol)
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

- [ ] ✓ TransactionValidator class implemented with all 10 business rules (BR-0001 through BR-0010) as discrete, testable methods.
- [ ] ✓ ISO 8583 message parser correctly extracts WS-ISO-FLD-004-AMOUNT, WS-TRANS-TYPE, and account identifier; rejects malformed messages.
- [ ] ✓ AccountRepository abstraction layer implemented; service does not directly access database; all account lookups and balance updates go through this layer.
- [ ] ✓ All numeric calculations use fixed-point arithmetic (BigDecimal or equivalent); no floating-point operations on monetary values.
- [ ] ✓ TC-0001 (Happy Path): valid transaction with amount within bounds, non-blacklisted account, sufficient balance, SPEI fee applied, VAT calculated, account debited, state transitioned to 30 (credit), transaction logged — PASSES.
- [ ] ✓ TC-0002 (Boundary): transaction amount exactly at WS-RULE-MIN-AMOUNT passes validation — PASSES.
- [ ] ✓ TC-0003 (Boundary): transaction amount one unit below WS-RULE-MIN-AMOUNT rejected with WS-ERR-INVALID-FORMAT — PASSES.
- [ ] ✓ TC-0004 (Boundary): transaction amount exactly at WS-RULE-MAX-TRANS passes validation — PASSES.
- [ ] ✓ TC-0005 (Boundary): transaction amount one unit above WS-RULE-MAX-TRANS rejected with WS-ERR-INVALID-FORMAT — PASSES.
- [ ] ✓ TC-0006 (Boundary): fraud risk score exactly 60 blocks transaction with WS-ERR-BLACKLISTED — PASSES.
- [ ] ✓ TC-0007 (Boundary): fraud risk score 59 allows transaction to proceed — PASSES.
- [ ] ✓ TC-0008 (Boundary): SPEI fee (WS-TRANS-TYPE == 2) set to 12.50; non-SPEI fee set to 0 — PASSES.
- [ ] ✓ TC-0009 (Boundary): account balance exactly equal to final debit amount (original + fee + VAT) allows transaction; account debited to zero — PASSES.
- [ ] ✓ TC-0010 (Boundary): account balance one unit below final debit amount rejected with WS-ERR-INSUFF-FUNDS, state transitioned to 40 (audit), account NOT debited — PASSES.
- [ ] ✓ TC-0011 (Functional): account with DB-ACC-ST == 'B' rejected with WS-ERR-BLACKLISTED, state transitioned to 40 (audit) — PASSES.
- [ ] ✓ TC-0012 (Functional): SPEI transfer (WS-TRANS-TYPE == 2) from NOMINA account (WS-ACC-TYPE == 'NOMINA') rejected with WS-ERR-AUTH-DENIED — PASSES.
- [ ] ✓ TC-0013 (Functional): non-SPEI transfer (WS-TRANS-TYPE != 2) from NOMINA account allowed to proceed — PASSES.
- [ ] ✓ TC-0014 (Equivalence): VAT calculation verified: VAT = (original_amount + fee) × WS-TAX-IVA; final = original_amount + fee + VAT — PASSES.
- [ ] ✓ TC-0015 (Integration): transaction state transitions correctly: validation state (3200) → debit state (3400) → credit state (30) — PASSES.
- [ ] ✓ TC-0016 (Integration): transaction state transitions to audit state (40) on blacklist or insufficient funds — PASSES.
- [ ] ✓ TC-0017 (Integration): account retrieval and balance update are atomic; if balance update fails, entire transaction rolls back — PASSES.
- [ ] ✓ TC-0018 (Integration): TransactionLogEngine receives complete transaction details (amount, fee, VAT, final amount, account ID, type, state, error codes, timestamp) — PASSES.
- [ ] ✓ All error paths log the specific error code and reason; no silent failures.
- [ ] ✓ Idempotency implemented: duplicate transaction requests (same transaction ID) are detected and rejected or return cached result.
- [ ] ✓ Concurrent balance updates protected via optimistic or pessimistic locking; no race conditions.
- [ ] ✓ Code review completed; all business rules traced to implementation; no rule is missing or incorrectly implemented.
- [ ] ✓ Unit tests written for each business rule and boundary condition; all tests pass.
- [ ] ✓ Integration tests written for state transitions, database atomicity, and transaction log engine integration; all tests pass.
- [ ] ✓ Performance tested: transaction processing completes within acceptable latency (e.g., < 500ms per transaction).
- [ ] ✓ Logging and audit trail verified: all transaction details, rejections, and state transitions are logged with timestamps and user/system identifiers.
- [ ] ✓ Documentation updated: API contract (request/response schema), error codes, state machine diagram, and deployment instructions provided.

## 8. Constraints & gotchas

- NUMERIC PRECISION: All monetary calculations (amount, fee, VAT, balance) must use fixed-point arithmetic (BigDecimal, Decimal128, or equivalent) with at least 2 decimal places. The legacy COBOL system likely uses COMP-3 (packed decimal); ensure the modern implementation does not introduce floating-point rounding errors that would cause balance discrepancies or failed reconciliation.
- FRAUD SCORE BOUNDARY: The rule states 'exceeds 60' (score > 60), not 'equals or exceeds'. A score of exactly 60 must PASS validation (TC-0006 expects score 60 to block, but re-reading the rule: 'If final score exceeds 60' means > 60, so score 60 should allow; however TC-0006 explicitly states 'fraud risk score exactly at threshold (60) blocks transaction' — this is a contradiction in the spec. RESOLVE: treat the acceptance test as authoritative; implement score >= 60 as blocking.)
- AMOUNT THRESHOLD COMPARISON: Minimum and maximum thresholds are inclusive boundaries (TC-0002 and TC-0004 expect exact-match amounts to PASS). Implement as: amount >= WS-RULE-MIN-AMOUNT AND amount <= WS-RULE-MAX-TRANS.
- STATE TRANSITION ORDERING: Blacklist check (BR-0001) must execute BEFORE amount validation; insufficient funds check (BR-0005) must execute AFTER fraud scoring but BEFORE account debit. The order matters because some paths transition to audit state (40) while others reject without state change. Preserve the exact sequence in the main flow.
- ACCOUNT BALANCE ATOMICITY: The balance update (step 11) and state transition (step 12) must be atomic within a single database transaction. If the balance update succeeds but state transition fails, the account is debited but transaction appears incomplete — this breaks reconciliation. Use database-level transactions or implement compensating logic.
- VAT CALCULATION FORMULA: The rule states 'Compute WS-VAT-AMOUNT as (WS-TEMP-AMOUNT + WS-FEE-AMOUNT) multiplied by WS-TAX-IVA. Then compute final WS-TEMP-AMOUNT as original amount plus VAT plus fee.' This is ambiguous: does WS-TEMP-AMOUNT refer to the original amount or an intermediate value? TC-0014 expects verification of '(amount + fee) × tax rate + original amount'. Implement as: VAT = (original_amount + fee) × tax_rate; final = original_amount + fee + VAT. Do NOT apply VAT to VAT (no compounding).
- EMPLOYEE ID PARITY CHECK: BR-0009 checks if PR-EMP-ID modulo 2 == 0 (even). Ensure the modulo operation is performed on the integer value of PR-EMP-ID, not a string representation. If PR-EMP-ID is null or missing, define behavior explicitly (reject or skip).
- BLACKLIST AND FRAUD SCORE BOTH TRIGGER WS-ERR-BLACKLISTED: Two different business failures (BR-0001 and BR-0008) map to the same error code. Ensure logs and audit trail distinguish between them (e.g., log message includes 'blacklist' vs. 'fraud score'). The caller receives the same error code but internal audit must show root cause.
- INSUFFICIENT FUNDS DOES NOT DEBIT: BR-0005 explicitly states 'do not debit account' if balance is insufficient. Ensure the balance update is skipped entirely; do not attempt a partial debit or zero-amount update. The account state must remain unchanged.
- HIGH BALANCE RISK FLAGGING (BR-0010) SCOPE: The rule says 'iterate through 100 accounts' — this is a full table scan. If the account table grows beyond 100 rows, clarify whether to scan only the first 100 or all accounts. For now, assume exactly 100 accounts exist. This operation is expensive and should not block the critical transaction path; implement as a separate batch job or async task.
- TRANSACTION LOG ENGINE INTEGRATION: The legacy system calls a transaction log engine (TRANS-LOG-ENGINE.CPY). The modern service must invoke an equivalent logging service or write to an audit table. Ensure the log includes: transaction ID, account ID, original amount, fee, VAT, final amount, state transitions, error codes, timestamp, and user/system identifier. Logs must be immutable and queryable for compliance.
- IDEMPOTENCY: If the same transaction request is submitted twice (e.g., due to network retry), the service must either reject the duplicate or return the same result without double-debiting. Implement idempotency via transaction ID deduplication (check if transaction ID already exists in log before processing).
- ENCODING AND FIELD EXTRACTION: ISO 8583 messages may use different encodings (ASCII, EBCDIC, BCD). The parser must correctly extract numeric fields and handle leading zeros. Ensure WS-ISO-FLD-004-AMOUNT is parsed as a numeric value, not a string.
- ACCOUNT RETRIEVAL FAILURE: If AccountRepository.findByAccountId() returns null or throws an exception, the service must fail fast with a clear error code (e.g., WS-ERR-ACCOUNT-NOT-FOUND) and NOT proceed to validation. Do not assume a default account or skip the check.
- CONCURRENT BALANCE UPDATES: If two transactions for the same account are processed concurrently, the second transaction may see a stale balance. Use optimistic locking (version field) or pessimistic locking (row lock) to ensure balance consistency. The legacy COBOL system likely uses file locking; the modern service must implement equivalent protection.
- EXCEPTION PATH LOGGING: Every rejection path (amount out of bounds, blacklist, insufficient funds, fraud score, account type restriction) must log the specific error code and reason. Do not silently fail; ensure audit trail shows why each transaction was rejected.

## 9. Implementation steps

1. 1. Create a TransactionValidator class with dependency injection for: AccountRepository (DB abstraction), FraudScoringEngine, TransactionLogEngine, and a configuration object holding WS-RULE-MIN-AMOUNT, WS-RULE-MAX-TRANS, WS-TAX-IVA constants.
2. 2. Implement ISO 8583 message parser to extract WS-ISO-FLD-004-AMOUNT, WS-TRANS-TYPE, and account identifier from incoming request; validate that all required fields are present and non-null before proceeding.
3. 3. Implement account lookup: call AccountRepository.findByAccountId() to retrieve customer and account record; if not found, return error immediately. Extract DB-ACC-ST (status), DB-ACC-BAL (balance), DB-ACC-RISK (risk level), and WS-ACC-TYPE from the retrieved record.
4. 4. Implement BR-0001 (Blacklist Check): if DB-ACC-ST == 'B', set error code to WS-ERR-BLACKLISTED, log warning, transition transaction state to 40 (audit), and return rejection response without further processing.
5. 5. Implement BR-0002 and BR-0003 (Amount Threshold Validation): compare WS-ISO-FLD-004-AMOUNT against WS-RULE-MIN-AMOUNT and WS-RULE-MAX-TRANS; if out of bounds, set error code to WS-ERR-INVALID-FORMAT, log warning, and return rejection without state transition.
6. 6. Implement BR-0004 (Account Type Restriction): if WS-ACC-TYPE == 'NOMINA' AND WS-TRANS-TYPE == 2, set error code to WS-ERR-AUTH-DENIED, log restriction message, and return rejection.
7. 7. Implement BR-0006 (SPEI Fee Calculation): if WS-TRANS-TYPE == 2, set WS-FEE-AMOUNT = 12.50; otherwise set WS-FEE-AMOUNT = 0. Store original amount separately to preserve it for VAT calculation.
8. 8. Implement BR-0007 (VAT and Total Calculation): compute WS-VAT-AMOUNT = (original_amount + WS-FEE-AMOUNT) × WS-TAX-IVA; then compute final WS-TEMP-AMOUNT = original_amount + WS-FEE-AMOUNT + WS-VAT-AMOUNT. Use BigDecimal or equivalent for all monetary calculations to avoid floating-point precision loss.
9. 9. Implement BR-0008 (Fraud Risk Scoring): initialize WS-FRAUD-RISK-SCORE = 0; add 40 points if original_amount > 50,000; add 30 points if DB-ACC-RISK > 2; if final score > 60, set error code to WS-ERR-BLACKLISTED, log fraud alert with score, and return rejection without state transition.
10. 10. Implement BR-0005 (Insufficient Funds Check): compare DB-ACC-BAL against WS-TEMP-AMOUNT (final debit amount); if DB-ACC-BAL < WS-TEMP-AMOUNT, set error code to WS-ERR-INSUFF-FUNDS, transition state to 40 (audit), log warning, and return rejection WITHOUT debiting the account.
11. 11. If all validations pass, debit the account: call AccountRepository.updateBalance(accountId, DB-ACC-BAL - WS-TEMP-AMOUNT) and ensure the update is atomic (use database transaction or optimistic locking with version check).
12. 12. Transition transaction state to 30 (credit state) in the transaction record.
13. 13. Implement BR-0009 (Employee Validation for Payroll): if PR-EMP-ID modulo 2 == 0, set PR-EMP-STATUS = 'P' (blocked) and log block message; otherwise set PR-EMP-STATUS = 'V' (valid). This applies only if transaction type indicates payroll processing.
14. 14. Implement BR-0010 (High Balance Risk Flagging): iterate through all 100 accounts in the database; for each account, if DB-ACC-BAL > 80,000, log high-risk alert with account number and balance. This may be implemented as a separate scheduled batch job or as part of a monitoring service, not in the critical path.
15. 15. Call TransactionLogEngine.logTransaction() with complete transaction details: original amount, fees, VAT, final amount, account ID, transaction type, state transitions, error codes (if any), and timestamp.
16. 16. Return success response with transaction ID, final amount debited, state (30), and confirmation message to caller.
17. 17. Wrap all database operations in a transaction boundary; on any validation failure before debit, roll back any state changes; on debit failure, roll back balance update and transition to error state.
18. 18. Add comprehensive error handling: catch and log all exceptions; map unexpected errors to a generic WS-ERR-SYSTEM-ERROR code; never expose internal stack traces to caller.

## 10. Out of scope (handled by sibling prompts)

- Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-message-processing
- Integrate services for 'Process and validate a financial transaction with compliance and fraud controls'
