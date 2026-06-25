"""Service configuration for payment-message-processing.

The business-rule constants are owned by payment-processing-core
(``RuleConfig``). This slice only overrides the fraud-block operator: prompt 02
(BR-0008 / TC-0006) blocks when the score is strictly greater than 60, whereas
prompt 01 blocks at greater-than-or-equal. Everything else is inherited so the
two slices stay numerically identical.
"""

from dataclasses import replace

from payment_processing_core import RuleConfig


def build_message_config(base: RuleConfig | None = None) -> RuleConfig:
    """Return the RuleConfig used by this service (fraud blocks on score > 60)."""
    return replace(base or RuleConfig(), fraud_block_strict=True)
