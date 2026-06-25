"""payment-integration: wave-1 end-to-end integration harness + tests.

Prompt 03 (INTEGRATE) verifies that payment-message-processing (HTTP front end,
prompt 02) and payment-processing-core (business rules + state machine, prompt
01) work together across the real HTTP boundary. No business logic is
reimplemented here; this package only composes the two slices and asserts the
end-to-end contract.
"""

from .harness import (
    IntegrationSystem,
    account,
    minor,
    request_payload,
    running_system,
)

__all__ = [
    "IntegrationSystem",
    "account",
    "minor",
    "request_payload",
    "running_system",
]
