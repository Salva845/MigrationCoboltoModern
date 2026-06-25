"""TransactionResponse DTO and error-code -> HTTP status mapping."""

from payment_processing_core import ErrorCode, TransactionResult

# Error code -> HTTP status (prompt 02, step 26). Codes outside the documented
# set fall back to a sensible status.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.OK: 200,
    ErrorCode.INVALID_FORMAT: 400,
    ErrorCode.BLACKLISTED: 403,
    ErrorCode.AUTH_DENIED: 403,
    ErrorCode.INSUFF_FUNDS: 409,
    ErrorCode.ACCOUNT_NOT_FOUND: 404,
    ErrorCode.SYSTEM_ERROR: 500,
}


def status_for(code: ErrorCode) -> int:
    return HTTP_STATUS.get(code, 500)


def to_response(result: TransactionResult, *, duplicate: bool = False) -> dict:
    """Map a core TransactionResult to the JSON response body."""
    body = {
        "transaction_id": result.transaction_id,
        "success": result.success,
        "status": result.error_code.value,
        "state": int(result.state),
        "account_id": result.account_id,
        "trans_type": result.trans_type,
        "original_amount": str(result.original_amount),
        "fee": str(result.fee),
        "vat": str(result.vat),
        "final_amount": str(result.final_amount),
        "message": result.message,
        "duplicate": duplicate,
        "timestamp": result.timestamp.isoformat(),
    }
    if not result.success:
        body["error_code"] = result.error_code.value
    return body
