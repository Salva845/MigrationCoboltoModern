"""Request / ISO 8583 message parsing (ISO-PARSER.CPY replacement).

Maps an inbound HTTP JSON payload to the fields needed downstream:
transaction id (idempotency key), the raw ISO 8583 data elements consumed by
``payment_processing_core.parse_iso8583`` (amount, transaction type, account id)
and an optional customer id used for the BR-0009 payroll check.
"""

from dataclasses import dataclass


class RequestValidationError(Exception):
    """Raised for structurally invalid requests (mapped to HTTP 400)."""


@dataclass(frozen=True)
class ParsedRequest:
    transaction_id: str
    iso_fields: dict
    customer_id: str | None


def _first(source: dict, *keys):
    for k in keys:
        if k in source and source[k] not in (None, ""):
            return source[k]
    return None


def parse_request(payload: object) -> ParsedRequest:
    """Validate the envelope and extract the routing fields.

    Field-level numeric/format validation (amount, transaction type) is delegated
    to the core ISO 8583 parser so both slices reject malformed messages
    identically.
    """
    if not isinstance(payload, dict):
        raise RequestValidationError("request body must be a JSON object")

    transaction_id = _first(payload, "transaction_id", "transactionId")
    if transaction_id is None:
        raise RequestValidationError("missing required field: transaction_id")

    # ISO data elements may be nested or supplied at the top level.
    raw = (
        _first(payload, "message", "iso8583", "fields")
        if isinstance(_first(payload, "message", "iso8583", "fields"), dict)
        else payload
    )

    customer_id = _first(payload, "customer_id", "customerId") or _first(
        raw, 103, "103", "DE103", "customer_id"
    )

    return ParsedRequest(
        transaction_id=str(transaction_id),
        iso_fields=raw,
        customer_id=str(customer_id) if customer_id is not None else None,
    )
