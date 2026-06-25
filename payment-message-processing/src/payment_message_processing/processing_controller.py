"""TransactionProcessingController: POST /transactions/process (BANK74 slice).

A thin HTTP-facing orchestrator over :class:`TransactionProcessor`. It parses the
request envelope (reusing the shared ISO 8583 parser), delegates the validation /
fee / debit / high-risk pipeline to the processor and maps the result to an HTTP
status + JSON body. It holds no business logic of its own.
"""

import logging

from payment_processing_core import AccountRepository, ErrorCode

from .controller import ControllerResponse
from .dto import status_for, to_processing_response
from .parser import RequestValidationError, parse_request
from .processing import ProcessingConfig, TransactionProcessor

logger = logging.getLogger("payment_message_processing")


class TransactionProcessingController:
    def __init__(
        self,
        account_repository: AccountRepository,
        config: ProcessingConfig | None = None,
        processor: TransactionProcessor | None = None,
    ):
        self.processor = processor or TransactionProcessor(
            repository=account_repository, config=config
        )

    def process(self, payload: object) -> ControllerResponse:
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
                    "state": 40,
                    "message": str(exc),
                },
            )

        result = self.processor.process(request.transaction_id, request.iso_fields)
        return ControllerResponse(status_for(result.error_code), to_processing_response(result))
