# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Domain errors, each mapping to one HTTP status at the API boundary.

Separate from FastAPI so the engine and the gateway stay usable from a CLI, a
batch job or a test without importing the web stack.
"""

from __future__ import annotations


class InsuranceDomainError(Exception):
    """Base class for errors a caller can act on."""

    status_code = 400


class NotFoundError(InsuranceDomainError):
    status_code = 404


class ClaimNotFoundError(NotFoundError):
    def __init__(self, claim_id: str) -> None:
        super().__init__(f"Claim {claim_id!r} not found")
        self.claim_id = claim_id


class PolicyNotFoundError(NotFoundError):
    def __init__(self, policy_id: str) -> None:
        super().__init__(f"Policy {policy_id!r} not found")
        self.policy_id = policy_id


class BeneficiaryNotFoundError(NotFoundError):
    def __init__(self, beneficiary_id: str) -> None:
        super().__init__(f"Beneficiary {beneficiary_id!r} not found")
        self.beneficiary_id = beneficiary_id


class DecisionNotFoundError(NotFoundError):
    def __init__(self, claim_id: str) -> None:
        super().__init__(f"Claim {claim_id!r} has no current decision")
        self.claim_id = claim_id


class PaymentNotFoundError(NotFoundError):
    def __init__(self, payment_id: str) -> None:
        super().__init__(f"Payment {payment_id!r} not found")
        self.payment_id = payment_id


class InvalidClaimError(InsuranceDomainError):
    """The submitted claim cannot be accepted as written."""

    status_code = 422


class DuplicateClaimError(InsuranceDomainError):
    status_code = 409


class PaymentNotAllowedError(InsuranceDomainError):
    """A payment was attempted against a decision that does not authorise it."""

    status_code = 409


class PaymentApprovalRequiredError(InsuranceDomainError):
    """The amount needs a named human approver at the gateway."""

    status_code = 403


class IdempotencyConflictError(InsuranceDomainError):
    """The idempotency key was reused for a different payment."""

    status_code = 409


class EvidenceIncompleteError(InsuranceDomainError):
    status_code = 409
