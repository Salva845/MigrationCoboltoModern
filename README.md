# Acme Payroll Modernisation — Migration Playbook

> Agent-ready migration prompts generated 2026-06-25T00:20:39+00:00.

Each prompt under `prompts/` is a self-contained instruction for an AI coding agent (Claude Code, Devin, Windsurf) — or a developer — to migrate one functional slice. Work through them in **wave order**: a service's prompts only become unblocked once its dependency services (earlier waves) are done.

## How to use a prompt

1. Pick the next work item for the current wave from the table below.
2. Open its prompt page, copy the whole file, and give it to your agent.
3. Drive the agent until the prompt's *Definition of done* is met and the listed tests pass.
4. Record the PR / session on the work item (tracked in Phase 4 — Development).

## Services by wave

| Wave | Service | Approach | Work items |
| --- | --- | --- | --- |
| 1 | [data-initialization](services/data-initialization.md) | CLUSTER_MIGRATION | 0 |
| 1 | [payment-message-processing](services/payment-message-processing.md) | STRANGLER_FIG | 4 |
| 1 | [payment-processing-core](services/payment-processing-core.md) | STRANGLER_FIG | 4 |

## Migration prompts

**Wave 1**

- [Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-processing-core](prompts/01-implement-process-and-validate-a-financial-transaction-with.md) _(IMPLEMENT)_
- [Implement 'Process and validate a financial transaction with compliance and fraud controls' in payment-message-processing](prompts/02-implement-process-and-validate-a-financial-transaction-with.md) _(IMPLEMENT)_
- [Integrate services for 'Process and validate a financial transaction with compliance and fraud controls'](prompts/03-integrate-services-for-process-and-validate-a-financial-tran.md) _(INTEGRATE)_
- [Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-message-processing](prompts/04-implement-process-a-financial-transaction-with-validation-fe.md) _(IMPLEMENT)_
- [Implement 'Process a financial transaction with validation, fee calculation, and balance update' in payment-processing-core](prompts/05-implement-process-a-financial-transaction-with-validation-fe.md) _(IMPLEMENT)_
- [Integrate services for 'Process a financial transaction with validation, fee calculation, and balance update'](prompts/06-integrate-services-for-process-a-financial-transaction-with.md) _(INTEGRATE)_
