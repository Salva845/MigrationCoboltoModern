"""Business-rule configuration (legacy SECURITY-RULES.CPY / CONSTANTS.CPY).

The original COBOL copybooks were not provided with this migration slice, so the
threshold values below are documented assumptions. They are isolated here so a
later equivalence pass can pin them to the legacy values without touching logic.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RuleConfig:
    # WS-RULE-MIN-AMOUNT / WS-RULE-MAX-TRANS (inclusive bounds, BR-0002/BR-0003)
    min_amount: Decimal = Decimal("1.00")
    max_amount: Decimal = Decimal("1000000.00")
    # WS-TAX-IVA (BR-0007)
    vat_rate: Decimal = Decimal("0.16")
    # SPEI flat fee (BR-0006)
    spei_fee: Decimal = Decimal("12.50")
    spei_trans_type: int = 2
    # Fraud scoring (BR-0008)
    fraud_high_amount: Decimal = Decimal("50000")
    fraud_high_amount_points: int = 40
    fraud_high_risk_level: int = 2
    fraud_high_risk_points: int = 30
    # Block when score crosses the threshold. Prompt 01 (payment-processing-core)
    # blocks at score >= threshold (TC-0006: 60 blocks). Prompt 02
    # (payment-message-processing) blocks at score > threshold (TC-0006: 60
    # allows). The operator is selected by ``fraud_block_strict``.
    fraud_block_threshold: int = 60
    fraud_block_strict: bool = False
    # High-balance flagging (BR-0010)
    high_balance_threshold: Decimal = Decimal("80000")
