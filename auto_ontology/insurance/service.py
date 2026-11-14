# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Application service for claims.

The API layer is a thin translation of these functions; the CLI and the tests
call them directly. That keeps the HTTP surface free of business logic and makes
the end-to-end scenario executable without a server.

Every mutation runs inside one transaction supplied by the caller's connection
scope, so a half-created claim or a decision without its audit event cannot be
committed.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, select

from auto_ontology.insurance import repository
from auto_ontology.insurance.db import new_id, now_utc
from auto_ontology.insurance.decision.engine import (
    DecisionOutcome,
    decide_claim,
    get_current_decision,
)
from auto_ontology.insurance.errors import (
    ClaimNotFoundError,
    InvalidClaimError,
    PolicyNotFoundError,
)
from auto_ontology.insurance.ontology.reasoner import UnknownCauseError, classify_cause
from auto_ontology.insurance.payments.gateway import (
    PaymentRecord,
    SimulatedPaymentGateway,
)
from auto_ontology.insurance.schema import insurance_claim, insurance_claim_evidence

#: Evidence a death claim is expected to carry. The API accepts any type, but
#: the decision's R11 rule requires the configured minimum.
DEFAULT_CLAIM_CHANNEL = "API"


def to_jsonable(value: Any) -> Any:
    """Recursively convert dates and decimals for JSON responses."""
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _next_claim_number(connection: Connection) -> str:
    """Human-readable sequential claim number, unique per year.

    ``CLM-2026-0003``. Sequential because a claims registry is read aloud and
    sorted by humans; a uuid would make the case file harder to discuss.
    """
    year = now_utc().year
    count = connection.execute(
        select(func.count()).select_from(insurance_claim)
    ).scalar_one()
    return f"CLM-{year}-{int(count) + 1:04d}"


def submit_claim(
    connection: Connection,
    *,
    policy_id: str,
    claimant_party_id: str,
    date_of_death: datetime,
    cause_code: str,
    death_certificate_id: str | None = None,
    channel: str = DEFAULT_CLAIM_CHANNEL,
    notes: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Register a death claim and its supporting evidence.

    The claim restates the insured and the cause rather than deriving them from
    the policy: a claim is an *assertion by a claimant*, and the whole point of
    the ontology checks is to compare that assertion with the policy and the
    certificate. Deriving it would make the check vacuous.
    """
    policy = repository.get_policy(connection, policy_id)
    if policy is None:
        raise PolicyNotFoundError(policy_id)
    if policy["status"] in {"SURRENDERED", "TERMINATED"}:
        raise InvalidClaimError(
            f"Policy {policy['policy_number']} is {policy['status']}; no death "
            "benefit can be claimed"
        )
    claimant = repository.get_party(connection, claimant_party_id)
    if claimant is None:
        raise InvalidClaimError(f"Claimant party {claimant_party_id!r} not found")
    try:
        classify_cause(cause_code)
    except UnknownCauseError as exc:
        raise InvalidClaimError(str(exc)) from exc
    if death_certificate_id is not None:
        if repository.get_certificate(connection, death_certificate_id) is None:
            raise InvalidClaimError(
                f"Death certificate {death_certificate_id!r} not found"
            )

    claim_id = new_id("CLM")
    claim_number = _next_claim_number(connection)
    submitted_at = now_utc()
    connection.execute(
        insurance_claim.insert().values(
            id=claim_id,
            claim_number=claim_number,
            policy_id=policy["id"],
            insured_person_id=policy["insured_person_id"],
            claimant_party_id=claimant_party_id,
            death_certificate_id=death_certificate_id,
            claim_type="DEATH",
            date_of_death=date_of_death,
            cause_code=cause_code,
            status="SUBMITTED",
            channel=channel,
            submitted_at=submitted_at,
            notes=notes,
            created_at=submitted_at,
        )
    )
    for item in evidence or []:
        document_ref = item.get("document_ref") or f"UPLOAD-{new_id('DOC')}"
        connection.execute(
            insurance_claim_evidence.insert().values(
                id=new_id("EVD"),
                claim_id=claim_id,
                evidence_type=item["evidence_type"],
                document_ref=document_ref,
                document_hash=item.get("document_hash")
                or "sha256:" + hashlib.sha256(document_ref.encode()).hexdigest(),
                status=item.get("status", "PENDING"),
                source=item.get("source", channel),
                received_at=submitted_at,
                verified_at=(
                    submitted_at if item.get("status") == "VERIFIED" else None
                ),
                verified_by=item.get("verified_by"),
                notes=item.get("notes"),
            )
        )
    from auto_ontology.insurance import audit

    audit.record_event(
        connection,
        actor_type="CLAIMANT",
        actor_id=claimant_party_id,
        action="CLAIM_SUBMITTED",
        entity_type="INSURANCE_CLAIM",
        entity_id=claim_id,
        correlation_id=claim_id,
        payload={
            "claim_number": claim_number,
            "policy_number": policy["policy_number"],
            "cause_code": cause_code,
            "date_of_death": date_of_death.isoformat(),
            "channel": channel,
        },
    )
    return claim_view(connection, claim_id)


def claim_view(connection: Connection, claim_id: str) -> dict[str, Any]:
    claim = repository.get_claim(connection, claim_id)
    if claim is None:
        raise ClaimNotFoundError(claim_id)
    policy = repository.get_policy(connection, claim["policy_id"])
    assert policy is not None
    insured = repository.get_insured(connection, policy["insured_person_id"])
    beneficiaries = repository.get_beneficiaries(connection, policy["id"])
    evidence = repository.get_evidence(connection, claim_id)
    certificate = (
        repository.get_certificate(connection, claim["death_certificate_id"])
        if claim.get("death_certificate_id")
        else None
    )
    decision = get_current_decision(connection, claim_id)
    payments = SimulatedPaymentGateway().list_for_claim(connection, claim_id)

    return to_jsonable(
        {
            "claim": {
                "claim_id": claim["id"],
                "claim_number": claim["claim_number"],
                "claim_type": claim["claim_type"],
                "status": claim["status"],
                "date_of_death": claim["date_of_death"],
                "cause_code": claim["cause_code"],
                "cause_of_death": classify_cause(claim["cause_code"]).to_dict(),
                "submitted_at": claim["submitted_at"],
                "channel": claim["channel"],
                "claimant_party_id": claim["claimant_party_id"],
            },
            "policy": {
                "policy_id": policy["id"],
                "policy_number": policy["policy_number"],
                "product": policy["product_name"],
                "effective_date": policy["effective_date"],
                "expiry_date": policy["expiry_date"],
                "face_amount": policy["face_amount"],
                "status": policy["status"],
                "contestability_days": policy["contestability_days"],
            },
            "insured": {
                "insured_person_id": policy["insured_person_id"],
                "name": repository.full_name(insured),
                "date_of_birth": insured["date_of_birth"] if insured else None,
            },
            "beneficiaries": [
                {
                    "beneficiary_id": b["beneficiary_id"],
                    "name": b["full_name"],
                    "relationship": b["relationship_to_insured"],
                    "designation_type": b["designation_type"],
                    "allocation_percent": b["allocation_percent"],
                    "verification_status": b["verification_status"],
                }
                for b in beneficiaries
            ],
            "death_certificate": (
                {
                    "certificate_id": certificate["id"],
                    "certificate_number": certificate["certificate_number"],
                    "cause_code": certificate["cause_code"],
                    "verification_status": certificate["verification_status"],
                }
                if certificate
                else None
            ),
            "evidence": [
                {
                    "evidence_type": e["evidence_type"],
                    "document_ref": e["document_ref"],
                    "status": e["status"],
                    "verified_by": e["verified_by"],
                }
                for e in evidence
            ],
            "decision": decision.to_dict() if decision else None,
            "payments": [p.to_dict() for p in payments],
            "links": {
                "self": f"/api/insurance/claims/{claim_id}",
                "decide": f"/api/insurance/claims/{claim_id}/decision",
                "graph": f"/api/insurance/claims/{claim_id}/graph",
                "pay": f"/api/insurance/claims/{claim_id}/payments",
            },
        }
    )


def run_decision(
    connection: Connection, claim_id: str, *, actor: str | None = None
) -> DecisionOutcome:
    return decide_claim(connection, claim_id, actor=actor)


def pay_claim(
    connection: Connection,
    claim_id: str,
    *,
    idempotency_key: str,
    actor: str,
    approver_id: str | None = None,
    beneficiary_id: str | None = None,
) -> PaymentRecord:
    return SimulatedPaymentGateway().initiate(
        connection,
        claim_id=claim_id,
        idempotency_key=idempotency_key,
        actor=actor,
        approver_id=approver_id,
        beneficiary_id=beneficiary_id,
    )


def list_claims(connection: Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    claims = repository.list_claims(connection, limit=limit)
    rows: list[dict[str, Any]] = []
    for claim in claims:
        policy = repository.get_policy(connection, claim["policy_id"])
        decision = get_current_decision(connection, claim["id"])
        rows.append(
            to_jsonable(
                {
                    "claim_id": claim["id"],
                    "claim_number": claim["claim_number"],
                    "policy_number": policy["policy_number"] if policy else None,
                    "status": claim["status"],
                    "date_of_death": claim["date_of_death"],
                    "cause_code": claim["cause_code"],
                    "decision_status": decision.status if decision else None,
                    "net_payout_amount": (
                        decision.net_payout_amount if decision else None
                    ),
                }
            )
        )
    return rows


def policy_semantics(connection: Connection, policy_id: str) -> dict[str, Any]:
    """Evaluate the semantic layer for a policy through its latest claim.

    A policy has no date of death of its own; the metric is defined against a
    claim event. A caller asking about a live policy gets the calculation for
    today's date, which is what a servicing agent actually wants ("how far into
    the contestability period are we?").
    """
    policy = repository.get_policy(connection, policy_id)
    if policy is None:
        raise PolicyNotFoundError(policy_id)
    latest = (
        connection.execute(
            select(insurance_claim)
            .where(insurance_claim.c.policy_id == policy_id)
            .order_by(insurance_claim.c.submitted_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if latest is None:
        raise InvalidClaimError(
            "Semantic evaluation requires a claim event; no claim exists for this policy"
        )
    from auto_ontology.insurance.semantic.engine import SemanticEngine

    return to_jsonable(SemanticEngine().evaluate(connection, latest["id"]).as_dict())


def claim_graph(connection: Connection, claim_id: str) -> dict[str, Any]:
    """Build (or rebuild) and return the claim's knowledge graph."""
    from auto_ontology.insurance.decision.context import build_context

    context = build_context(connection, claim_id, persist=True)
    graph = context.graph
    return to_jsonable(
        {
            **graph.to_dict(),
            "evidence_chain": (
                context.evidence_chain.to_dict() if context.evidence_chain else None
            ),
            "evidence_chain_text": (
                context.evidence_chain.describe() if context.evidence_chain else None
            ),
            "ontology_violations": context.graph_violations,
            "statements": graph.statements(),
        }
    )


def audit_trail(connection: Connection, claim_id: str) -> dict[str, Any]:
    from auto_ontology.insurance import audit

    chain = audit.verify_chain(connection, claim_id)
    from auto_ontology.insurance.db import rows
    from auto_ontology.insurance.schema import insurance_audit_event

    events = rows(
        connection,
        select(insurance_audit_event)
        .where(insurance_audit_event.c.correlation_id == claim_id)
        .order_by(insurance_audit_event.c.occurred_at),
    )
    return to_jsonable(
        {
            **chain,
            "events": [
                {
                    "event_id": event["id"],
                    "occurred_at": event["occurred_at"],
                    "actor": f"{event['actor_type']}:{event['actor_id']}",
                    "action": event["action"],
                    "entity_type": event["entity_type"],
                    "entity_id": event["entity_id"],
                    "payload": event["payload"],
                    "event_hash": event["event_hash"],
                    "previous_hash": event["previous_hash"],
                }
                for event in events
            ],
        }
    )


__all__ = [
    "audit_trail",
    "claim_graph",
    "claim_view",
    "list_claims",
    "pay_claim",
    "policy_semantics",
    "run_decision",
    "submit_claim",
    "to_jsonable",
]
