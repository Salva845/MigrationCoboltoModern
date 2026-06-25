# Service: payment-message-processing

[← Playbook index](../README.md)

Owns the parsing, validation, and normalization of ISO 8583 payment messages into canonical internal representations. Provides message structure definitions, validation rules, and constants for downstream transaction processing.

**Bounded context:** ISO 8583 payment message parsing, validation, and transformation for financial transaction interchange
**Migration wave:** 1
**Approach:** STRANGLER_FIG

**Shared data:** ACCOUNT, CONSTANTS, COREVARS, CUSTOMER, DATABASE, DATETIME, ISO-BITMAP-LOGIC, ISO-PARSER, ISO8583, MESSAGES, REPORT-TEMPLATES, REPORTS, SECURITY-RULES, SYSTEM-SPECS, TRANS-LOG-ENGINE, VALIDATION

## Work items

- [Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-message-processing](../prompts/02-implement-process-and-validate-a-financial-transaction-with.md) _(IMPLEMENT)_
- [Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-message-processing](../prompts/04-implement-process-a-financial-transaction-with-validation-fe.md) _(IMPLEMENT)_
- [Integrate services for 'Process a financial transaction with validation, fee calculation, and balance update'](../prompts/06-integrate-services-for-process-a-financial-transaction-with.md) _(INTEGRATE)_
- Validate equivalence: 'Process a financial transaction with validation, fee calculation, and balance update' _(TEST)_
