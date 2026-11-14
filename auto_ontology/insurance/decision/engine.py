# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The claim decision engine.

Pipeline, in order, all deterministic:

1. **Semantic layer** computes certified values (days, benefit, payout).
2. **Knowledge graph** is projected from the relational facts and validated
   against the ontology.
3. **Ontology** classifies the cause, matches exclusions and checks its
   constraints.
4. **Rules** consume those results and produce PASS/FAIL with a consequence.
5. **Outcome** is aggregated: a DENY-severity failure denies, any other
   failure refers, everything else auto-approves.
6. **Persistence** writes one decision, one approval-history row and one audit
   event in a single transaction.

Step 5 is intentionally conservative: `AUTO_APPROVED` requires *every*
applicable rule to pass, and any uncertainty becomes `MANUAL_REVIEW`. The
machine never resolves an ambiguity in favour of paying, and never denies on
anything except an explicit contractual exclusion or a fully encumbered policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select, update

from auto_ontology.insurance import __version__ as ENGINE_VERSION
from auto_ontology.insurance import audit
from auto_ontology.insurance.config import InsuranceSettings, get_settings
from auto_ontology.insurance.db import new_id, now_utc
from auto_ontology.insurance.decision.context import (
    DecisionContext,
    build_context,
)
from auto_ontology.insurance.decision.explain import build_explanation
from auto_ontology.insurance.decision.rules import (
    DENY,
    FAIL,
    PASS,
    SKIP,
    RULES_VERSION,
    RuleOutcome,
    evaluate_rules,
)
from auto_ontology.insurance.errors import InvalidClaimError
from auto_ontology.insurance.types import as_money
from auto_ontology.insurance.schema import (
    insurance_claim,
    insurance_claim_approval_history,
    insurance_decision_result,
)

AUTO_APPROVED = "AUTO_APPROVED"
MANUAL_REVIEW = "MANUAL_REVIEW"
DENIED = "DENIED"

CLAIM_STATUS_BY_DECISION = {
    AUTO_APPROVED: "APPROVED",
    MANUAL_REVIEW: "UNDER_REVIEW",
    DENIED: "DENIED",
}


@dataclass
class DecisionOutcome:
    """The stored decision, in the shape the API returns it."""

    id: str
    claim_id: str
    policy_id: str
    status: str
    net_payout_amount: Decimal
    currency: str
    confidence_score: float
    rules_applied: list[dict[str, Any]]
    semantic_calculations: dict[str, Any]
    ontology_validation: list[dict[str, Any]]
    kg_evidence: dict[str, Any]
    explanation: str
    decision_path: str
    audit_reference: str
    engine_version: str
    decided_at: datetime
    decided_by: str
    sections: dict[str, Any] = field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.status == AUTO_APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "policy_id": self.policy_id,
            "decision_id": self.id,
            "approval_status": self.status,
            "net_payout_amount": str(as_money(self.net_payout_amount)),
            "currency": self.currency,
            "confidence_score": self.confidence_score,
            "decision_rules_applied": self.rules_applied,
            "semantic_calculations": self.semantic_calculations,
            "ontology_validation_results": self.ontology_validation,
            "knowledge_graph_evidence": self.kg_evidence,
            "decision_explanation": self.explanation,
            "decision_sections": self.sections,
            "decision_path": self.decision_path,
            "audit_reference": self.audit_reference,
            "engine_version": self.engine_version,
            "rules_version": RULES_VERSION,
            "decided_at": self.decided_at.isoformat(),
            "decided_by": self.decided_by,
        }


def _aggregate(outcomes: list[RuleOutcome]) -> tuple[str, str]:
    """Turn rule outcomes into a status and a one-line decision path."""
    blocking = [
        outcome
        for outcome in outcomes
        if outcome.status == FAIL
        and outcome.definition.on_fail_action in {DENY, "REFER"}
    ]
    denials = [
        outcome for outcome in blocking if outcome.definition.on_fail_action == DENY
    ]
    if denials:
        return (
            DENIED,
            "DENIED: " + "; ".join(f"{o.definition.id} - {o.message}" for o in denials),
        )
    if blocking:
        return (
            MANUAL_REVIEW,
            "MANUAL_REVIEW: "
            + "; ".join(f"{o.definition.id} - {o.message}" for o in blocking),
        )
    return (
        AUTO_APPROVED,
        f"AUTO_APPROVED: all {len(outcomes)} rules passed "
        "(policy, ontology, graph, evidence, and limit checks).",
    )


def _confidence(outcomes: list[RuleOutcome]) -> float:
    """Share of applicable rules that passed, rounded for reporting.

    Deliberately not a model probability. It is a coverage measure of the rule
    set, which is what a reviewer can reason about: 1.0 means every contractual
    and evidentiary check passed, not "97% likely to be fraud-free".
    """
    applicable = [outcome for outcome in outcomes if outcome.status != SKIP]
    if not applicable:
        return 0.0
    return round(sum(1 for o in applicable if o.status == PASS) / len(applicable), 4)


def _kg_evidence(context: DecisionContext) -> dict[str, Any]:
    graph = context.graph
    class_counts: dict[str, int] = {}
    for node in graph.nodes:
        class_counts[node.class_name] = class_counts.get(node.class_name, 0) + 1
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "class_counts": class_counts,
        "evidence_chain": (
            context.evidence_chain.to_dict() if context.evidence_chain else None
        ),
        "evidence_chain_text": (
            context.evidence_chain.describe() if context.evidence_chain else None
        ),
        "statements": graph.statements(),
        "ontology_violations": context.graph_violations,
        "competing_claims": [c["claim_number"] for c in context.competing_claims],
        "restrictions": [
            {"type": r["restriction_type"], "description": r["description"]}
            for r in context.restrictions
        ],
    }


def decide_claim(
    connection: Connection,
    claim_id: str,
    *,
    actor: str | None = None,
    settings: InsuranceSettings | None = None,
) -> DecisionOutcome:
    """Adjudicate one claim and persist the result atomically."""
    settings = settings or get_settings()
    claim = (
        connection.execute(
            select(insurance_claim).where(insurance_claim.c.id == claim_id)
        )
        .mappings()
        .first()
    )
    if claim is None:
        from auto_ontology.insurance.errors import ClaimNotFoundError

        raise ClaimNotFoundError(claim_id)
    if claim["status"] == "PAID":
        raise InvalidClaimError(
            f"Claim {claim_id!r} is already paid; a new decision would not be payable"
        )

    context = build_context(connection, claim_id, settings=settings, persist=True)
    outcomes = evaluate_rules(context)
    status, decision_path = _aggregate(outcomes)
    payout = (
        as_money(context.semantic.get("payout_amount"))
        if status == AUTO_APPROVED
        else Decimal("0.00")
    )
    confidence = _confidence(outcomes)

    audit_reference = (
        "AUD-"
        + audit.sha256_hex(
            {
                "claim": claim_id,
                "policy": context.policy["id"],
                "status": status,
                "payout": str(payout),
                "rules": [
                    {"id": o.definition.id, "status": o.status} for o in outcomes
                ],
                "semantic": {
                    name: str(value.value)
                    for name, value in context.semantic.values.items()
                },
            }
        )[:16].upper()
    )

    explanation = build_explanation(
        context=context,
        outcomes=outcomes,
        status=status,
        payout=as_money(context.semantic.get("payout_amount")),
        audit_reference=audit_reference,
        engine_version=ENGINE_VERSION,
    )

    decided_by = actor or settings.system_actor
    decision_id = new_id("DEC")
    decided_at = now_utc()
    new_claim_status = CLAIM_STATUS_BY_DECISION[status]

    connection.execute(
        update(insurance_decision_result)
        .where(insurance_decision_result.c.claim_id == claim_id)
        .values(is_current=False)
    )
    connection.execute(
        insurance_decision_result.insert().values(
            id=decision_id,
            claim_id=claim_id,
            policy_id=context.policy["id"],
            status=status,
            is_current=True,
            net_payout_amount=payout,
            currency=settings.currency,
            confidence_score=confidence,
            rules_applied=[o.to_dict() for o in outcomes],
            semantic_calculations=context.semantic.as_dict(),
            ontology_validation=[r.to_dict() for r in context.ontology],
            kg_evidence=_kg_evidence(context),
            explanation=explanation.text,
            decision_path=decision_path,
            audit_reference=audit_reference,
            engine_version=ENGINE_VERSION,
            decided_at=decided_at,
            decided_by=decided_by,
        )
    )
    connection.execute(
        insurance_claim_approval_history.insert().values(
            id=new_id("APH"),
            claim_id=claim_id,
            decision_result_id=decision_id,
            from_status=claim["status"],
            to_status=new_claim_status,
            action=f"DECISION_{status}",
            actor_type="SYSTEM" if decided_by == settings.system_actor else "HUMAN",
            actor_id=decided_by,
            approval_level=(
                "AUTOMATED"
                if status == AUTO_APPROVED
                else "PENDING_HUMAN"
                if status == MANUAL_REVIEW
                else "SYSTEM_DENIAL"
            ),
            reason=decision_path,
            created_at=decided_at,
        )
    )
    connection.execute(
        update(insurance_claim)
        .where(insurance_claim.c.id == claim_id)
        .values(status=new_claim_status)
    )
    audit.record_event(
        connection,
        actor_type="SYSTEM" if decided_by == settings.system_actor else "HUMAN",
        actor_id=decided_by,
        action=f"CLAIM_DECISION_{status}",
        entity_type="INSURANCE_CLAIM",
        entity_id=claim_id,
        correlation_id=claim_id,
        payload={
            "decision_id": decision_id,
            "audit_reference": audit_reference,
            "status": status,
            "payout": str(payout),
            "currency": settings.currency,
            "engine_version": ENGINE_VERSION,
            "rules_version": RULES_VERSION,
            "failed_rules": [o.definition.id for o in outcomes if o.status == FAIL],
        },
    )

    return DecisionOutcome(
        id=decision_id,
        claim_id=claim_id,
        policy_id=context.policy["id"],
        status=status,
        net_payout_amount=payout,
        currency=settings.currency,
        confidence_score=confidence,
        rules_applied=[o.to_dict() for o in outcomes],
        semantic_calculations=context.semantic.as_dict(),
        ontology_validation=[r.to_dict() for r in context.ontology],
        kg_evidence=_kg_evidence(context),
        explanation=explanation.text,
        decision_path=decision_path,
        audit_reference=audit_reference,
        engine_version=ENGINE_VERSION,
        decided_at=decided_at,
        decided_by=decided_by,
        sections=explanation.sections,
    )


def get_current_decision(
    connection: Connection, claim_id: str
) -> DecisionOutcome | None:
    """Read the claim's current decision back as the API's shape."""
    row = (
        connection.execute(
            select(insurance_decision_result)
            .where(
                insurance_decision_result.c.claim_id == claim_id,
                insurance_decision_result.c.is_current.is_(True),
            )
            .order_by(insurance_decision_result.c.decided_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return DecisionOutcome(
        id=row["id"],
        claim_id=row["claim_id"],
        policy_id=row["policy_id"],
        status=row["status"],
        net_payout_amount=row["net_payout_amount"],
        currency=row["currency"],
        confidence_score=row["confidence_score"],
        rules_applied=row["rules_applied"],
        semantic_calculations=row["semantic_calculations"],
        ontology_validation=row["ontology_validation"],
        kg_evidence=row["kg_evidence"],
        explanation=row["explanation"],
        decision_path=row["decision_path"],
        audit_reference=row["audit_reference"],
        engine_version=row["engine_version"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
    )
