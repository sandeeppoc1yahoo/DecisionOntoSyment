# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Certified business definitions for life insurance claims.

This is the semantic layer's *catalog*: each definition states, in business and
formulaic terms, what a number means. The engine implements each name exactly
once, and nothing else in the codebase may recompute it.

Why a catalog rather than helper functions? Because a payout of "$250,000" is
an assertion about *net death benefit*, and the next reader needs to know
whether that already deducted the policy loan, whether it included interest,
and which version of the definition was in force. A function name cannot carry
that; a certified definition with a version and an owner can.

The four kinds are a deliberate distinction:

``PARAMETER``
    A contractual fact read from the policy row (face amount, contestability
    days). The semantic layer does not compute it; it publishes it.
``FORMULA``
    Arithmetic over the claim and policy (active days, outstanding loan
    balance, net death benefit).
``DERIVED``
    Arithmetic over other definitions (payout, coverage test). Depending on
    other definitions, not on raw columns, keeps one definition of each number.
``CATEGORICAL``
    A business classification (claim eligibility) whose value is a label
    rather than a figure, with the reasons attached.

``depends_on`` is not decoration: the engine evaluates in dependency order and
refuses a cycle at import time, and the API can show a downstream consumer what
will change if a definition is re-versioned.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SemanticDefinition:
    name: str
    display_name: str
    description: str
    definition_kind: str
    formula: str
    unit: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    version: str = "1.0.0"
    owner: str = "claims-domain"
    is_certified: bool = True


SEMANTIC_DEFINITIONS: tuple[SemanticDefinition, ...] = (
    SemanticDefinition(
        name="policy_effective_date",
        display_name="Policy Effective Date",
        description="The date cover commenced under the contract.",
        definition_kind="PARAMETER",
        formula="policy.effective_date",
        unit="date",
    ),
    SemanticDefinition(
        name="policy_expiry_date",
        display_name="Policy Expiry Date",
        description="The date cover ends under the contract.",
        definition_kind="PARAMETER",
        formula="policy.expiry_date",
        unit="date",
    ),
    SemanticDefinition(
        name="policy_active_days",
        display_name="Policy Active Days",
        description=(
            "Whole days between the policy effective date and the date of death. "
            "The measure of how long cover had run when the insured died."
        ),
        definition_kind="FORMULA",
        formula="(date_of_death - policy.effective_date).days",
        unit="days",
        depends_on=("policy_effective_date",),
    ),
    SemanticDefinition(
        name="contestability_period_days",
        display_name="Contestability Period",
        description=(
            "Days from the effective date during which the insurer may contest "
            "the contract for misrepresentation. Two years (730 days) on this "
            "product."
        ),
        definition_kind="PARAMETER",
        formula="policy.contestability_days",
        unit="days",
        depends_on=("policy_effective_date",),
    ),
    SemanticDefinition(
        name="past_contestability",
        display_name="Past Contestability Period",
        description=(
            "Whether the policy has been in force longer than its contestability "
            "period on the date of death. Contestability-only exclusions do not "
            "apply once this is true."
        ),
        definition_kind="DERIVED",
        formula="policy_active_days > contestability_period_days",
        unit="boolean",
        depends_on=("policy_active_days", "contestability_period_days"),
    ),
    SemanticDefinition(
        name="policy_face_amount",
        display_name="Policy Face Amount",
        description="The death benefit stated on the policy schedule, before deductions.",
        definition_kind="PARAMETER",
        formula="policy.face_amount",
        unit="currency",
    ),
    SemanticDefinition(
        name="outstanding_policy_loan_balance",
        display_name="Outstanding Policy Loans",
        description=(
            "Sum of outstanding loan balances secured by the policy. Repaid "
            "loans contribute zero; they are not netted a second time."
        ),
        definition_kind="FORMULA",
        formula="SUM(loan.outstanding_balance WHERE loan.status = 'OUTSTANDING')",
        unit="currency",
    ),
    SemanticDefinition(
        name="net_death_benefit",
        display_name="Net Death Benefit",
        description=(
            "Face amount less outstanding policy loans. The amount available to "
            "distribute to beneficiaries before any legal restriction."
        ),
        definition_kind="DERIVED",
        formula="policy_face_amount - outstanding_policy_loan_balance",
        unit="currency",
        depends_on=("policy_face_amount", "outstanding_policy_loan_balance"),
    ),
    SemanticDefinition(
        name="primary_beneficiary_count",
        display_name="Primary Beneficiary Count",
        description="Number of active primary beneficiary designations.",
        definition_kind="FORMULA",
        formula="COUNT(beneficiary WHERE designation_type = 'PRIMARY')",
        unit="count",
    ),
    SemanticDefinition(
        name="total_primary_allocation_percent",
        display_name="Total Primary Allocation",
        description=(
            "Sum of active primary allocation percentages. Must equal 100 for a "
            "complete designation."
        ),
        definition_kind="FORMULA",
        formula="SUM(beneficiary.allocation_percent WHERE designation_type = 'PRIMARY')",
        unit="percent",
    ),
    SemanticDefinition(
        name="payout_amount",
        display_name="Claim Payout Amount",
        description=(
            "Net death benefit multiplied by the primary allocation. With a "
            "single 100% primary beneficiary this equals the net death benefit."
        ),
        definition_kind="DERIVED",
        formula="net_death_benefit * total_primary_allocation_percent / 100",
        unit="currency",
        depends_on=("net_death_benefit", "total_primary_allocation_percent"),
    ),
    SemanticDefinition(
        name="policy_in_force_at_death",
        display_name="Policy In Force At Death",
        description=(
            "Whether the policy was in force and the date of death fell inside "
            "the effective-to-expiry window."
        ),
        definition_kind="CATEGORICAL",
        formula=(
            "policy.status = 'IN_FORCE' AND policy.effective_date <= date_of_death "
            "<= policy.expiry_date"
        ),
        unit="boolean",
        depends_on=("policy_effective_date", "policy_expiry_date"),
    ),
    SemanticDefinition(
        name="claim_eligibility",
        display_name="Claim Eligibility",
        description=(
            "Business classification of whether the death is a covered event "
            "under the contract. Eligibility is about cover; it is not the "
            "decision (exclusions, evidence and limits are adjudicated later)."
        ),
        definition_kind="CATEGORICAL",
        formula=(
            "ELIGIBLE when policy_in_force_at_death; NOT_COVERED when the death "
            "is outside the coverage window; POLICY_NOT_IN_FORCE when the "
            "contract had lapsed or been surrendered"
        ),
        unit="category",
        depends_on=("policy_in_force_at_death",),
    ),
    SemanticDefinition(
        name="death_benefit_by_beneficiary",
        display_name="Death Benefit by Beneficiary",
        description=(
            "Net death benefit apportioned across active primary beneficiaries "
            "by allocation percentage, rounded to cents with any residual "
            "assigned to the last beneficiary."
        ),
        definition_kind="DERIVED",
        formula="net_death_benefit * allocation_percent / 100 per beneficiary",
        unit="currency",
        depends_on=("net_death_benefit", "total_primary_allocation_percent"),
    ),
)

DEFINITIONS_BY_NAME: dict[str, SemanticDefinition] = {
    definition.name: definition for definition in SEMANTIC_DEFINITIONS
}


def validate_registry() -> None:
    """Fail fast on an unknown dependency or a cycle.

    Run at import time by the engine module: a semantic layer that silently
    evaluates a stale number because a definition moved is worse than one that
    refuses to start.
    """
    for definition in SEMANTIC_DEFINITIONS:
        for dependency in definition.depends_on:
            if dependency not in DEFINITIONS_BY_NAME:
                raise ValueError(
                    f"Semantic definition {definition.name!r} depends on unknown "
                    f"{dependency!r}"
                )

    resolved: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in resolved:
            return
        if name in visiting:
            raise ValueError(f"Semantic definition cycle at {name!r}")
        visiting.add(name)
        for dependency in DEFINITIONS_BY_NAME[name].depends_on:
            visit(dependency)
        visiting.discard(name)
        resolved.add(name)

    for definition in SEMANTIC_DEFINITIONS:
        visit(definition.name)
