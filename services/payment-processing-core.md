# Service: payment-processing-core

[← Playbook index](../README.md)

Handles ISO 8583 bitmap parsing, transaction logging, security rule validation, and report generation for payment transactions. This service encapsulates the core payment processing logic that bridges message parsing with audit and compliance requirements.

**Bounded context:** ISO 8583 payment message processing, transaction logging, and security rule enforcement for financial transactions
**Data store:** PostgreSQL
**Migration wave:** 1
**Approach:** STRANGLER_FIG

**Shared data:** ACCOUNT, CONSTANTS, COREVARS, CUSTOMER, DATABASE, DATETIME, ISO-BITMAP-LOGIC, ISO-PARSER, ISO8583, MESSAGES, REPORT-TEMPLATES, REPORTS, SECURITY-RULES, SYSTEM-SPECS, TRANS-LOG-ENGINE, VALIDATION

## Work items

- [Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-processing-core](../prompts/01-implement-process-and-validate-a-financial-transaction-with.md) _(IMPLEMENT)_
- [Integrate services for 'Process and validate a financial transaction with compliance and fraud controls'](../prompts/03-integrate-services-for-process-and-validate-a-financial-tran.md) _(INTEGRATE)_
- Validate equivalence: 'Process and validate a financial transaction with compliance and fraud controls' _(TEST)_
- [Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-processing-core](../prompts/05-implement-process-a-financial-transaction-with-validation-fe.md) _(IMPLEMENT)_
