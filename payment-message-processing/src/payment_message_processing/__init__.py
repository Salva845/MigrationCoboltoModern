"""payment-message-processing service (migrated from COBOL BANK85).

Wave 1 / prompt 02: a REST front end that validates ISO 8583 financial
transactions. It reuses payment-processing-core for the ten business rules,
state machine, atomic debit and idempotency, adding only the HTTP layer, request
parsing, customer/employee resolution (BR-0009) and response mapping.
"""

from .config import build_message_config
from .controller import ControllerResponse, TransactionValidationController
from .customer import Customer, CustomerRepository, InMemoryCustomerRepository
from .dto import HTTP_STATUS, status_for, to_processing_response, to_response
from .http_app import build_server, make_handler
from .parser import ParsedRequest, RequestValidationError, parse_request
from .processing import (
    ProcessingConfig,
    ProcessingResult,
    TransactionProcessor,
)
from .processing_controller import TransactionProcessingController

__all__ = [
    "HTTP_STATUS",
    "ControllerResponse",
    "Customer",
    "CustomerRepository",
    "InMemoryCustomerRepository",
    "ParsedRequest",
    "ProcessingConfig",
    "ProcessingResult",
    "RequestValidationError",
    "TransactionProcessingController",
    "TransactionProcessor",
    "TransactionValidationController",
    "build_message_config",
    "build_server",
    "make_handler",
    "parse_request",
    "status_for",
    "to_processing_response",
    "to_response",
]
