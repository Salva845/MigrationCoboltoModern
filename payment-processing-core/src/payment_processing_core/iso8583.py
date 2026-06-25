"""Minimal ISO 8583 field extraction (ISO-PARSER / ISO-BITMAP-LOGIC replacement).

Only the fields needed by this slice are extracted: DE004 (amount), the
transaction/processing type, and the account identifier. Malformed messages
(missing or non-numeric fields) are rejected.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .errors import ErrorCode, TransactionError

# DE004 is a 12-digit amount expressed in minor units (cents), per ISO 8583.
_AMOUNT_MINOR_UNITS = Decimal("100")


@dataclass(frozen=True)
class Iso8583Message:
    amount: Decimal  # WS-ISO-FLD-004-AMOUNT
    trans_type: int  # WS-TRANS-TYPE
    account_id: str  # account identifier


def parse_iso8583(fields: dict) -> Iso8583Message:
    """Extract the required fields from a decoded ISO 8583 message.

    ``fields`` maps data-element numbers (as int or str keys) to raw values:
      - 4  -> amount in minor units (string of digits or int)
      - 3  -> processing/transaction type
      - 102 -> account identifier
    """

    def _get(*keys):
        for k in keys:
            if k in fields and fields[k] not in (None, ""):
                return fields[k]
        return None

    raw_amount = _get(4, "4", "DE004", "amount")
    raw_type = _get(3, "3", "DE003", "trans_type")
    raw_account = _get(102, "102", "DE102", "account_id")

    if raw_amount is None or raw_type is None or raw_account is None:
        raise TransactionError(
            ErrorCode.INVALID_FORMAT, "malformed ISO 8583 message: missing required field(s)"
        )

    try:
        # DE004 carries minor units with leading zeros; scale to major units.
        amount = (Decimal(str(raw_amount)) / _AMOUNT_MINOR_UNITS).quantize(Decimal("0.01"))
        trans_type = int(str(raw_type))
    except (InvalidOperation, ValueError) as exc:
        raise TransactionError(
            ErrorCode.INVALID_FORMAT, f"malformed ISO 8583 numeric field: {exc}"
        ) from exc

    return Iso8583Message(amount=amount, trans_type=trans_type, account_id=str(raw_account))
