"""Fraud scoring engine (BR-0008).

Injected into the validator so the scoring heuristic can evolve independently and
so boundary tests can substitute a deterministic stub.
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from .config import RuleConfig


class FraudScoringEngine(ABC):
    @abstractmethod
    def score(self, amount: Decimal, risk_level: int) -> int:
        """Return the fraud risk score for a transaction."""


class DefaultFraudScoringEngine(FraudScoringEngine):
    def __init__(self, config: RuleConfig | None = None):
        self.config = config or RuleConfig()

    def score(self, amount: Decimal, risk_level: int) -> int:
        c = self.config
        score = 0
        if amount > c.fraud_high_amount:
            score += c.fraud_high_amount_points
        if risk_level > c.fraud_high_risk_level:
            score += c.fraud_high_risk_points
        return score
