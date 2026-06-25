"""TransactionValidationController: POST /transactions/validate.

A thin HTTP-facing orchestrator over payment-processing-core. It resolves the
employee id for the BR-0009 payroll check, delegates the ten business rules,
state machine, atomic debit and idempotency to the core ``TransactionValidator``
(so rules are never reimplemented here), runs the BR-0010 monitoring batch
best-effort, and maps the result to an HTTP status + JSON body.
"""

import logging
from dataclasses import dataclass

from payment_processing_core import (
    AccountRepository,
    ErrorCode,
    FraudScoringEngine,
    InMemoryTransactionLogEngine,
    RuleConfig,
    TransactionLogEngine,
    TransactionValidator,
)

from .config import build_message_config
from .customer import CustomerRepository, InMemoryCustomerRepository
from .dto import status_for, to_response
from .parser import ParsedRequest, RequestValidationError, parse_request

logger = logging.getLogger("payment_message_processing")


@dataclass(frozen=True)
class ControllerResponse:
    status_code: int
    body: dict


class TransactionValidationController:
    def __init__(
        self,
        account_repository: AccountRepository,
        customer_repository: CustomerRepository | None = None,
        log_engine: TransactionLogEngine | None = None,
        fraud_engine: FraudScoringEngine | None = None,
        config: RuleConfig | None = None,
    ):
        self.customer_repository = customer_repository or InMemoryCustomerRepository()
        self.log_engine = log_engine or InMemoryTransactionLogEngine()
        self.validator = TransactionValidator(
            repository=account_repository,
            log_engine=self.log_engine,
            fraud_engine=fraud_engine,
            config=build_message_config(config),
        )

    def validate(self, payload: object) -> ControllerResponse:
        try:
            request = parse_request(payload)
        except RequestValidationError as exc:
            logger.warning("rejected malformed request: %s", exc)
            return ControllerResponse(
                status_for(ErrorCode.INVALID_FORMAT),
                {
                    "success": False,
                    "status": ErrorCode.INVALID_FORMAT.value,
                    "error_code": ErrorCode.INVALID_FORMAT.value,
                    "state": 3200,
                    "message": str(exc),
                },
            )

        employee_id = self._resolve_employee_id(request)
        duplicate = self.log_engine.exists(request.transaction_id)

        result = self.validator.process(
            request.transaction_id, request.iso_fields, employee_id=employee_id
        )
        self._run_high_balance_report()

        return ControllerResponse(
            status_for(result.error_code), to_response(result, duplicate=duplicate)
        )

    def _resolve_employee_id(self, request: ParsedRequest) -> int | None:
        # No customer context -> not a payroll transaction, skip BR-0009.
        if request.customer_id is None:
            return None
        customer = self.customer_repository.find_by_customer_id(request.customer_id)
        if customer is None:
            # Gotcha: customer not found -> skip employee validation.
            logger.info("customer %s not found; skipping BR-0009", request.customer_id)
            return None
        return customer.employee_id

    def _run_high_balance_report(self) -> None:
        # BR-0010 is informational and must never affect the transaction outcome.
        try:
            self.validator.br0010_high_balance_report()
        except Exception:  # noqa: BLE001 - monitoring side-effect only.
            logger.exception("BR-0010 high-balance report failed (ignored)")
