# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic explanation of a claim decision.

Why not an LLM narrative? Because the explanation is part of the decision, and
the decision must be identical on a re-run. A template driven by the recorded
values is checkable: every sentence maps to a `SemanticValue`, an
`OntologyValidation` or a `RuleOutcome`, and a reviewer can trace it back without
the text having been "generated" by anything.

The explanation has three audiences:

* the **claimant**, who needs a plain statement of what was decided and why;
* the **reviewer**, who needs the calculations and the failing rule, in order;
* the **auditor**, who needs the rule versions, the ontology inference chain,
  the graph path and the audit reference - the evidence, not the prose.

One text field cannot serve all three well, so the API returns `explanation`
(text) plus the same content structured in `semantic_calculations`,
`ontology_validation`, `kg_evidence` and `rules_applied`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from auto_ontology.insurance.decision.context import DecisionContext
from auto_ontology.insurance.decision.rules import FAIL, PASS, RuleOutcome


@dataclass(frozen=True)
class Explanation:
    text: str
    sections: dict[str, Any]


def _money(value: Any) -> str:
    return f"${Decimal(str(value)):,.2f}"


def build_explanation(
    *,
    context: DecisionContext,
    outcomes: list[RuleOutcome],
    status: str,
    payout: Decimal,
    audit_reference: str,
    engine_version: str,
) -> Explanation:
    semantic = context.semantic
    classification = context.classification
    claim = context.claim
    policy = context.policy
    primary = [
        d
        for d in context.beneficiary_designations
        if d["designation_type"] == "PRIMARY"
    ]
    beneficiary_text = (
        f"{primary[0]['full_name']} as the sole verified primary beneficiary "
        f"at {primary[0]['allocation_percent']}%"
        if primary
        else "no primary beneficiary"
    )

    failed = [o for o in outcomes if o.status == FAIL]
    passed = [o for o in outcomes if o.status == PASS]

    headline = {
        "AUTO_APPROVED": (
            f"Claim {claim['claim_number']} under policy "
            f"{policy['policy_number']} is AUTO_APPROVED for {_money(payout)}, "
            f"payable to {beneficiary_text}."
        ),
        "MANUAL_REVIEW": (
            f"Claim {claim['claim_number']} under policy "
            f"{policy['policy_number']} is referred for MANUAL REVIEW; "
            "no payment is authorised."
        ),
        "DENIED": (
            f"Claim {claim['claim_number']} under policy "
            f"{policy['policy_number']} is DENIED; no benefit is payable."
        ),
    }[status]

    calculation_lines = [
        f"Policy active days: {semantic['policy_active_days'].display_value} "
        f"({semantic['policy_effective_date'].display_value} to "
        f"{claim['date_of_death']:%Y-%m-%d}).",
        f"Contestability period: {semantic['contestability_period_days'].display_value}; "
        f"passed = {str(semantic.get('past_contestability')).lower()}.",
        f"Face amount {semantic['policy_face_amount'].display_value} less "
        f"outstanding loans "
        f"{semantic['outstanding_policy_loan_balance'].display_value} = "
        f"net death benefit {semantic['net_death_benefit'].display_value}.",
        f"Primary allocation {semantic['total_primary_allocation_percent'].display_value} "
        f"across {semantic['primary_beneficiary_count'].value} beneficiary; "
        f"calculated payout {semantic['payout_amount'].display_value}.",
    ]

    ontology_lines = [
        f"{result.code}: {result.status} - {result.evidence}"
        for result in context.ontology
    ]
    ontology_lines.append(f"Cause classification: {classification.inference}.")

    chain = context.evidence_chain
    graph_lines = [
        f"Chain: {chain.describe()}"
        if chain
        else "Chain: no insured->policy->beneficiary path.",
        f"Graph: {len(context.graph.nodes)} nodes, {len(context.graph.edges)} edges; "
        f"{len(context.graph_violations)} ontology violation(s).",
    ]
    if context.graph_violations:
        graph_lines.extend(
            f"  ! {violation['violation']}: {violation['detail']}"
            for violation in context.graph_violations
        )

    rule_lines = [
        f"  {outcome.definition.id}: {outcome.status} - {outcome.message}"
        for outcome in outcomes
    ]

    text_lines = [
        headline,
        "",
        "WHY",
        *(f"- {line}" for line in calculation_lines),
        (
            f"- Cause {classification.code} is classified as "
            f"{classification.class_label}, a "
            f"{'covered' if not context.matched_exclusions else 'excluded'} cause."
        ),
        (
            f"- Evidence chain: {chain.describe()}."
            if chain
            else "- Evidence chain could not be established."
        ),
        "",
        "ONTOLOGY VALIDATION",
        *(f"- {line}" for line in ontology_lines),
        "",
        "KNOWLEDGE GRAPH EVIDENCE",
        *(f"- {line}" for line in graph_lines),
        "",
        "DECISION RULES APPLIED",
        *rule_lines,
        "",
        "AUDIT",
        f"- Reference {audit_reference}; engine {engine_version}; "
        f"{len(passed)}/{len(outcomes)} rules passed"
        + (
            f"; blocking: {', '.join(o.definition.id for o in failed)}"
            if failed
            else ""
        )
        + ".",
    ]

    if status == "MANUAL_REVIEW" and failed:
        text_lines.insert(
            1,
            "Reason(s) for referral: "
            + "; ".join(f"{o.definition.name} - {o.message}" for o in failed),
        )

    sections = {
        "headline": headline,
        "calculations": calculation_lines,
        "ontology": ontology_lines,
        "knowledge_graph": graph_lines,
        "rules": [outcome.to_dict() for outcome in outcomes],
        "audit_reference": audit_reference,
        "engine_version": engine_version,
    }
    return Explanation(text="\n".join(text_lines), sections=sections)
