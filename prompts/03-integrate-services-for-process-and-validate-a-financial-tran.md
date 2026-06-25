[← Playbook index](../README.md)
 · [Service: payment-processing-core](../services/payment-processing-core.md)

# Migration prompt — Integrate services for 'Process and validate a financial transaction with compliance and fraud controls'

> You are migrating one functional slice of a legacy system to its modern replacement. Implement **only** what this prompt covers; preserve behavior exactly. Sibling slices are handled by other prompts (see *Out of scope*).

## 1. Objective

Implement the transaction validation and processing orchestration in payment-processing-core as a stateful service that accepts ISO 8583 transaction messages, applies 10 business rules (amount thresholds, blacklist checks, account type restrictions, fraud risk scoring, balance verification, fee/VAT calculation), transitions transactions through state machines (validation → debit → credit or audit), and logs all details to the transaction log engine. This service is the core of BANK85's workflow and must preserve exact numeric calculations, state transition semantics, and exception routing.

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

- [ ] ✓ TransactionRequest and TransactionResponse DTOs are defined and match the ISO 8583 schema and legacy error codes.
- [ ] ✓ Account retrieval logic queries the database and populates DB-ACC-ST, DB-ACC-TYPE, DB-ACC-BAL, DB-ACC-RISK, and account index without race conditions.
- [ ] ✓ BR-0002 (minimum amount) and BR-0003 (maximum amount) are implemented; WS-RULE-MIN-AMOUNT and WS-RULE-MAX-TRANS are extracted from SYSTEM-SPECS.CPY and used in comparisons.
- [ ] ✓ BR-0001 (blacklist check) rejects transactions with status 'B' and transitions to audit state (40).
- [ ] ✓ BR-0004 (SPEI account type restriction) blocks SPEI transfers from NOMINA accounts and allows non-SPEI transfers from NOMINA accounts.
- [ ] ✓ BR-0006 (SPEI fee) sets fee to 12.50 for transaction type 2, else 0.00; fee is stored as decimal with 2 decimal places.
- [ ] ✓ BR-0007 (VAT and total) computes VAT = (amount + fee) × tax_rate and final_debit_amount = amount + fee + VAT using decimal arithmetic; WS-TAX-IVA is extracted from SYSTEM-SPECS.CPY.
- [ ] ✓ BR-0005 (insufficient funds) checks balance before debiting; if balance < final_debit_amount, rejects and transitions to audit state (40) without debiting.
- [ ] ✓ BR-0008 (fraud risk scoring) initializes score to 0, adds 40 if amount > 50,000, adds 30 if account risk > 2, blocks if score > 60 (not >=).
- [ ] ✓ BR-0009 (employee validation) checks if employee ID is even and sets status to 'P' (blocked) if true; this is a pre-flight check.
- [ ] ✓ BR-0010 (high balance risk) iterates through 100 accounts and logs alerts for balances > 80,000 after transaction completes.
- [ ] ✓ Debit operation is atomic: balance is decremented by final_debit_amount, state transitions to 30 (credit), and success is returned only after both operations commit.
- [ ] ✓ State machine transitions are explicit: 3200 (validation) → 3400 (debit) → 30 (credit) on success; 3200 → 40 (audit) on failure. Transitions are persisted in the database.
- [ ] ✓ Database transaction wraps the entire operation: BEGIN on entry, COMMIT on success, ROLLBACK on any validation failure or debit failure.
- [ ] ✓ Transaction log engine receives complete transaction details (amount, fees, VAT, final debit, account ID, status code, fraud score, state) after state transition commits.
- [ ] ✓ All legacy error codes (WS-ERR-INVALID-FORMAT, WS-ERR-BLACKLISTED, WS-ERR-AUTH-DENIED, WS-ERR-INSUFF-FUNDS, WS-SUCCESS) are mapped to HTTP status codes and included in response payloads.
- [ ] ✓ TC-0001 (happy path) passes: valid transaction processes end-to-end with correct fees, VAT, and credit state transition.
- [ ] ✓ TC-0002 (amount == min) passes: transaction at minimum threshold is accepted.
- [ ] ✓ TC-0003 (amount == min - 1) passes: transaction below minimum threshold is rejected with WS-ERR-INVALID-FORMAT.
- [ ] ✓ TC-0004 (amount == max) passes: transaction at maximum threshold is accepted.
- [ ] ✓ TC-0005 (amount == max + 1) passes: transaction above maximum threshold is rejected with WS-ERR-INVALID-FORMAT.
- [ ] ✓ TC-0006 (fraud score == 60) passes: transaction with score exactly 60 is allowed.
- [ ] ✓ TC-0007 (fraud score == 59) passes: transaction with score 59 is allowed.
- [ ] ✓ TC-0008 (SPEI fee) passes: transaction type 2 incurs 12.50 fee; non-SPEI incurs 0 fee.
- [ ] ✓ TC-0009 (balance == final debit) passes: account with balance exactly equal to final debit amount is accepted.
- [ ] ✓ TC-0010 (balance == final debit - 1) passes: account with balance one unit below final debit amount is rejected with WS-ERR-INSUFF-FUNDS and transitions to audit state (40).
- [ ] ✓ TC-0011 (blacklist) passes: account with status 'B' is rejected with WS-ERR-BLACKLISTED and transitions to audit state (40).
- [ ] ✓ TC-0012 (SPEI from NOMINA) passes: SPEI transfer from NOMINA account is rejected with WS-ERR-AUTH-DENIED.
- [ ] ✓ TC-0013 (non-SPEI from NOMINA) passes: non-SPEI transfer from NOMINA account is allowed.
- [ ] ✓ TC-0014 (VAT formula) passes: final amount = original + fee + (original + fee) × tax_rate; verified with multiple test cases.
- [ ] ✓ TC-0015 (state transitions) passes: transaction transitions from 3200 (validation) → 3400 (debit) → 30 (credit) on success.
- [ ] ✓ TC-0016 (audit state) passes: transaction transitions to 40 (audit) on blacklist or insufficient funds.
- [ ] ✓ TC-0017 (database atomicity) passes: account balance is updated and state is transitioned atomically; no partial updates on failure.
- [ ] ✓ TC-0018 (transaction log) passes: transaction log engine receives complete transaction details including amount, fees, VAT, final debit, account ID, status code, fraud score, and state.
- [ ] ✓ All copybook field names and types are mapped to the target service's data model; no field is omitted or mistyped.
- [ ] ✓ Decimal arithmetic is used throughout; no floating-point calculations.
- [ ] ✓ Idempotency is implemented: duplicate transactions are detected and not processed twice.
- [ ] ✓ Error messages and status codes match the legacy system exactly.
- [ ] ✓ Code is reviewed by a domain expert familiar with BANK85 and the legacy system.
- [ ] ✓ Integration tests verify end-to-end flow with a test database; no mocks for database or transaction log engine.
- [ ] ✓ Performance is acceptable: transaction processing completes within SLA (typically < 500ms for validation + debit + log).

## 8. Constraints & gotchas

- NUMERIC PRECISION: VAT and fee calculations must use decimal arithmetic (e.g., Java BigDecimal, Python Decimal) with at least 2 decimal places. Floating-point arithmetic will cause rounding errors that fail TC-0014. The formula is (amount + fee) × tax_rate + amount, not (amount + fee + amount) × tax_rate.
- FRAUD RISK BOUNDARY: The threshold is score > 60, not >= 60. A score of exactly 60 must allow the transaction (TC-0006 vs. TC-0007). Off-by-one errors here will fail boundary tests.
- AMOUNT THRESHOLD BOUNDARIES: Minimum and maximum thresholds are inclusive on the passing side. Amount == min passes (TC-0002); amount == min - 1 fails (TC-0003). Amount == max passes (TC-0004); amount == max + 1 fails (TC-0005). Extract exact threshold values from SYSTEM-SPECS.CPY; do not hardcode.
- INSUFFICIENT FUNDS ATOMICITY: If balance < final_debit_amount, the account must NOT be debited and state must transition to audit (40), not credit (30). A common error is debiting first, then checking balance. The check must precede the debit operation.
- SPEI FEE CONDITIONAL: Fee of 12.50 applies only if WS-TRANS-TYPE == 2 (SPEI). Non-SPEI transactions (type != 2) must have fee = 0.00. This affects VAT calculation downstream (TC-0008).
- ACCOUNT TYPE RESTRICTION LOGIC: BR-0004 requires BOTH conditions: account type == 'NOMINA' AND transaction type == 2. If either is false, the transaction is allowed. A NOMINA account can process non-SPEI transfers (TC-0013).
- BLACKLIST STATE TRANSITION: Blacklisted accounts (status 'B') must transition to audit state (40), not be rejected silently. The audit state is a distinct outcome from validation failure (TC-0011, TC-0016).
- DATABASE RETRIEVAL ORDERING: Account details must be retrieved before any validation checks. If account does not exist, the service must handle gracefully (not in acceptance tests, but a production gotcha).
- STATE TRANSITION PERSISTENCE: State transitions (3200 → 3400 → 30 or 3200 → 40) must be persisted atomically with balance updates. If the service crashes between state update and balance update, the transaction is in an inconsistent state. Use database transactions or event sourcing.
- TRANSACTION LOG ORDERING: The transaction log engine must receive the log entry AFTER the state transition is committed, not before. If logging fails, the transaction should not be rolled back (log is secondary), but the log entry must include the final state.
- EMPLOYEE ID PARITY: BR-0009 checks if PR-EMP-ID modulo 2 == 0 (even). This is a pre-flight check for payroll transactions, separate from the main transaction flow. Ensure it is evaluated before amount validation.
- HIGH BALANCE RISK ITERATION: BR-0010 iterates through 100 accounts and logs alerts for each with balance > 80,000. This is a post-transaction operation and must not block the main transaction. Implement as a background job or async log write.
- ISO 8583 FIELD EXTRACTION: WS-ISO-FLD-004-AMOUNT and WS-TRANS-TYPE must be extracted from the ISO 8583 message using the bitmap logic in ISO-BITMAP-LOGIC.CPY. Do not assume fields are in a fixed position; use the bitmap to locate them.
- ERROR CODE MAPPING: Legacy error codes (WS-ERR-*) must map consistently to HTTP status codes. WS-ERR-INVALID-FORMAT and WS-ERR-AUTH-DENIED typically map to 400 Bad Request; WS-ERR-BLACKLISTED and WS-ERR-INSUFF-FUNDS to 403 Forbidden or 422 Unprocessable Entity. Document the mapping.
- IDEMPOTENCY: If the same transaction is submitted twice (same account, amount, timestamp), the service must not debit twice. Implement idempotency via transaction ID deduplication or database unique constraints on (account_id, transaction_id, timestamp).
- COPYBOOK SCHEMA ALIGNMENT: The legacy copybooks (DATABASE.CPY, SYSTEM-SPECS.CPY, SECURITY-RULES.CPY) define field names and types. Map these exactly to the target service's data model. If a field is COMP-3 (packed decimal) in COBOL, ensure the target service uses the same precision.

## 9. Implementation steps

1. 1. Extract and model the ISO 8583 field schema from BANK85.CBL and ISO-BITMAP-LOGIC.CPY: map WS-ISO-FLD-004-AMOUNT, WS-TRANS-TYPE, and account identifier fields to strongly-typed request DTOs in the target service.
2. 2. Create a TransactionRequest DTO with fields: iso_amount, transaction_type, account_id, customer_id, and a TransactionResponse DTO with fields: status_code, final_debit_amount, fraud_risk_score, transaction_state, error_message.
3. 3. Implement account retrieval logic that queries the legacy database (via DATABASE.CPY schema) to fetch DB-ACC-ST (account status), DB-ACC-TYPE, DB-ACC-BAL (balance), DB-ACC-RISK (risk level), and account index for the given account_id. Cache or retrieve atomically to prevent TOCTOU race conditions.
4. 4. Implement BR-0002 and BR-0003 (amount threshold validation): extract WS-RULE-MIN-AMOUNT and WS-RULE-MAX-TRANS from SYSTEM-SPECS.CPY; compare iso_amount against both bounds; if out of bounds, set status_code to WS-ERR-INVALID-FORMAT and return rejection without proceeding to debit.
5. 5. Implement BR-0001 (blacklist check): after account retrieval, check if DB-ACC-ST == 'B'; if true, set status_code to WS-ERR-BLACKLISTED, transition transaction state to 40 (audit), and return rejection without debiting.
6. 6. Implement BR-0004 (SPEI account type restriction): check if WS-ACC-TYPE == 'NOMINA' AND WS-TRANS-TYPE == 2; if true, set status_code to WS-ERR-AUTH-DENIED and return rejection.
7. 7. Implement BR-0006 (SPEI fee calculation): check if WS-TRANS-TYPE == 2; if true, set WS-FEE-AMOUNT to 12.50 (as decimal, not integer); otherwise set to 0.00. Store in a mutable variable for use in VAT calculation.
8. 8. Implement BR-0007 (VAT and total calculation): extract WS-TAX-IVA from SYSTEM-SPECS.CPY (likely 0.16 for Mexican IVA); compute WS-VAT-AMOUNT = (iso_amount + WS-FEE-AMOUNT) × WS-TAX-IVA; compute final_debit_amount = iso_amount + WS-FEE-AMOUNT + WS-VAT-AMOUNT. Use decimal arithmetic (not floating-point) to preserve precision to at least 2 decimal places.
9. 9. Implement BR-0005 (insufficient funds check): compare DB-ACC-BAL against final_debit_amount; if balance < final_debit_amount, set status_code to WS-ERR-INSUFF-FUNDS, transition state to 40 (audit), and return rejection WITHOUT debiting the account.
10. 10. Implement BR-0008 (fraud risk scoring): initialize WS-FRAUD-RISK-SCORE to 0; add 40 points if iso_amount > 50,000; add 30 points if DB-ACC-RISK > 2; if final score > 60 (not >=), set status_code to WS-ERR-BLACKLISTED, include fraud_risk_score in response, and return rejection. Note: score == 60 must allow transaction (TC-0006 boundary).
11. 11. Implement BR-0009 (employee validation for payroll): if processing a payroll transaction, check if PR-EMP-ID modulo 2 == 0 (even); if true, set PR-EMP-STATUS to 'P' and reject; otherwise set to 'V'. This is a pre-flight check before main transaction flow.
12. 12. Implement the happy-path debit operation: after all validations pass, atomically subtract final_debit_amount from DB-ACC-BAL in the database; transition transaction state to 30 (credit); return status_code WS-SUCCESS.
13. 13. Implement BR-0010 (high balance risk flagging): after debit, iterate through up to 100 accounts in the database; for each account with DB-ACC-BAL > 80,000, log a high-risk alert with account number and balance to the transaction log engine (via TRANS-LOG-ENGINE.CPY).
14. 14. Integrate with the transaction log engine (TRANS-LOG-ENGINE.CPY): after state transition (credit or audit), log the complete transaction record including: original amount, fees, VAT, final debit amount, account identifier, status code, fraud score, and transaction state. Ensure log entry is written before returning success to caller.
15. 15. Implement state machine transitions: define explicit state transitions (validation state 3200 → debit state 3400 → credit state 30 on success; validation state 3200 → audit state 40 on failure). Persist state transitions in the database or event log for auditability.
16. 16. Wrap the entire transaction processing in a database transaction (BEGIN TRANSACTION → COMMIT on success, ROLLBACK on any validation failure) to ensure atomicity of balance updates and state transitions.
17. 17. Map all legacy error codes (WS-ERR-INVALID-FORMAT, WS-ERR-BLACKLISTED, WS-ERR-AUTH-DENIED, WS-ERR-INSUFF-FUNDS) to HTTP status codes and response payloads in the REST/gRPC contract of payment-processing-core.
18. 18. Create unit tests for each business rule in isolation (BR-0001 through BR-0010), then integration tests for state transitions and the happy path.
19. 19. Execute all 18 acceptance tests (TC-0001 through TC-0018) against the implemented service, paying special attention to boundary conditions (TC-0002 through TC-0010) and state transition atomicity (TC-0015 through TC-0017).
20. 20. Validate that the transaction log engine receives complete transaction details (TC-0018) by inspecting log output or querying the log table after each transaction.

## 10. Out of scope (handled by sibling prompts)

- Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-processing-core
- Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-message-processing
