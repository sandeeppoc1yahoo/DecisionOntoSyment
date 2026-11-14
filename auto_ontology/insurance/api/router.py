# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Claims processing and payment REST surface.

Every route is a thin wrapper over `auto_ontology.insurance.service`. The
business rules live in the domain, not here, so the same logic is reachable
from the CLI, a batch job or a test without HTTP.

Mounted at ``/api/insurance``. Authentication is the platform gateway's job -
the Next.js app forwards the caller's identity and this service is not publicly
reachable - matching how the rest of Auto-Ontology treats the FastAPI tier. The
``actor`` fields default to the system identity; a deployment should overwrite
them from the authenticated request rather than trusting the body.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from auto_ontology.insurance import __version__
from auto_ontology.insurance.api.errors import domain_errors
from auto_ontology.insurance.bootstrap import bootstrap, reset_and_seed
from auto_ontology.insurance.config import get_settings
from auto_ontology.insurance.db import connection_scope, transaction
from auto_ontology.insurance.decision.engine import get_current_decision
from auto_ontology.insurance.decision.store import load_decision_rules
from auto_ontology.insurance.ontology.reasoner import ontology_summary
from auto_ontology.insurance.ontology.store import load_ontology
from auto_ontology.insurance.payments.gateway import (
    SIMULATION_NOTICE,
    SimulatedPaymentGateway,
)
from auto_ontology.insurance.semantic.catalog import load_semantic_definitions
from auto_ontology.insurance import service

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class EvidenceInput(BaseModel):
    """One supporting document supplied with a claim."""

    evidence_type: str = Field(
        description="DEATH_CERTIFICATE, CLAIM_FORM, IDENTITY_PROOF, BENEFICIARY_PROOF..."
    )
    document_ref: str | None = Field(
        default=None, description="Storage reference; a synthetic one is generated."
    )
    status: Literal["PENDING", "VERIFIED", "REJECTED", "WAIVED"] = "PENDING"
    source: str = "API"
    verified_by: str | None = None
    notes: str | None = None


class ClaimSubmission(BaseModel):
    """A death claim as a claimant or intake system submits it."""

    policy_id: str = Field(examples=["POL-1001"])
    claimant_party_id: str = Field(examples=["PRT-1002"])
    date_of_death: datetime = Field(examples=["2026-06-14T00:00:00"])
    cause_code: str = Field(
        description="ICD-10 code; must resolve to an ontology class.",
        examples=["I21.9"],
    )
    death_certificate_id: str | None = Field(default=None, examples=["DC-9001"])
    channel: str = "API"
    notes: str | None = None
    evidence: list[EvidenceInput] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    actor: str | None = Field(
        default=None, description="Overrides the system actor on the audit trail."
    )


class PaymentRequest(BaseModel):
    """A request to settle an approved claim.

    ``idempotency_key`` is required: a retried HTTP call must return the first
    transaction rather than pay twice. ``approver_id`` is required above the
    gateway's automated limit.
    """

    idempotency_key: str = Field(min_length=8, examples=["claim-CLM-2001-payout-1"])
    approver_id: str | None = Field(
        default=None, examples=["officer:j.mensah"], description="Named human approver."
    )
    beneficiary_id: str | None = None
    actor: str = "officer:claims-intake"


# ---------------------------------------------------------------------------
# Reads: ontology, semantics, rules
# ---------------------------------------------------------------------------
@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness plus the storage target and engine version."""
    settings = get_settings()
    return {
        "status": "ok",
        "engine_version": __version__,
        "storage": settings.db_url.split("://", 1)[0],
        "currency": settings.currency,
        "auto_approval_limit": str(settings.auto_approval_limit),
        "payment_human_approval_limit": str(settings.payment_human_approval_limit),
        "payment_simulation": SIMULATION_NOTICE,
    }


@router.get("/ontology")
def ontology() -> dict[str, Any]:
    """The life-insurance T-Box: classes, properties, constraints, cause map."""
    with connection_scope() as connection:
        return {
            "catalog": ontology_summary(),
            "materialized": load_ontology(connection),
        }


@router.get("/semantic/definitions")
def semantic_definitions() -> dict[str, Any]:
    """The certified semantic catalog, with formulas and versions."""
    with connection_scope() as connection:
        return {
            "definitions": service.to_jsonable(load_semantic_definitions(connection))
        }


@router.get("/rules")
def decision_rules() -> dict[str, Any]:
    """The persisted decision-rule catalog."""
    with connection_scope() as connection:
        return {"rules": service.to_jsonable(load_decision_rules(connection))}


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------
@router.post("/claims", status_code=201)
def submit_claim(body: ClaimSubmission) -> dict[str, Any]:
    """Register a death claim and its evidence.

    The claim restates the insured and the cause on purpose: adjudication exists
    to compare the claimant's assertion with the policy and the certificate, so
    deriving it would make the comparison meaningless.

    422 for an unknown cause code, a missing certificate or a surrendered policy.
    """
    with domain_errors(), transaction() as connection:
        return service.submit_claim(
            connection,
            policy_id=body.policy_id,
            claimant_party_id=body.claimant_party_id,
            date_of_death=body.date_of_death,
            cause_code=body.cause_code,
            death_certificate_id=body.death_certificate_id,
            channel=body.channel,
            notes=body.notes,
            evidence=[item.model_dump() for item in body.evidence],
        )


@router.get("/claims")
def list_claims(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Claims with their current decision status."""
    with connection_scope() as connection:
        claims = service.list_claims(connection, limit=limit)
    return {"claims": claims, "count": len(claims)}


@router.get("/claims/{claim_id}")
def get_claim(claim_id: str) -> dict[str, Any]:
    """Everything a reviewer needs: the claim, policy, evidence, decision, payments."""
    with domain_errors(), connection_scope() as connection:
        return service.claim_view(connection, claim_id)


@router.post("/claims/{claim_id}/decision")
def decide(claim_id: str, body: DecisionRequest | None = None) -> dict[str, Any]:
    """Adjudicate the claim and persist the decision.

    Runs the semantic calculations, rebuilds and validates the knowledge graph,
    validates the ontology constraints, applies the rule set, then writes the
    decision, approval history, claim status and audit event in one transaction.

    Idempotent in effect but not in storage: re-running writes a new current
    decision and supersedes the previous one, which is how a re-adjudication
    after new evidence should be recorded.
    """
    actor = body.actor if body else None
    with domain_errors(), transaction() as connection:
        outcome = service.run_decision(connection, claim_id, actor=actor)
        return outcome.to_dict()


@router.get("/claims/{claim_id}/decision")
def current_decision(claim_id: str) -> dict[str, Any]:
    """The claim's current decision, with its full explanation."""
    with domain_errors(), connection_scope() as connection:
        outcome = get_current_decision(connection, claim_id)
        if outcome is None:
            from auto_ontology.insurance.errors import DecisionNotFoundError

            raise DecisionNotFoundError(claim_id)
        return outcome.to_dict()


@router.get("/claims/{claim_id}/graph")
def claim_graph(claim_id: str) -> dict[str, Any]:
    """The claim's knowledge graph, rebuilt from the current facts.

    Rebuilding on read makes the endpoint safe to use during review: a
    restriction added after the decision appears without re-adjudicating.
    """
    with domain_errors(), transaction() as connection:
        return service.claim_graph(connection, claim_id)


@router.get("/claims/{claim_id}/audit")
def claim_audit(claim_id: str) -> dict[str, Any]:
    """The claim's audit events with hash-chain verification."""
    with domain_errors(), connection_scope() as connection:
        return service.audit_trail(connection, claim_id)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
@router.post("/claims/{claim_id}/payments", status_code=201)
def create_payment(claim_id: str, body: PaymentRequest) -> dict[str, Any]:
    """Authorise and settle the approved benefit by simulated direct deposit.

    Requires a current AUTO_APPROVED decision. Returns the original transaction
    with ``idempotent_replay: true`` when the same key is reused for the same
    payment; 409 when the key was used for a different one; 403 when the amount
    needs a named human approver and none was supplied.
    """
    with domain_errors(), transaction() as connection:
        payment = service.pay_claim(
            connection,
            claim_id,
            idempotency_key=body.idempotency_key,
            actor=body.actor,
            approver_id=body.approver_id,
            beneficiary_id=body.beneficiary_id,
        )
        return payment.to_dict()


@router.get("/claims/{claim_id}/payments")
def list_payments(claim_id: str) -> dict[str, Any]:
    """Every payment attempted for the claim."""
    with domain_errors(), connection_scope() as connection:
        payments = SimulatedPaymentGateway().list_for_claim(connection, claim_id)
        return {"payments": [p.to_dict() for p in payments]}


@router.get("/payments/{payment_id}")
def get_payment(payment_id: str) -> dict[str, Any]:
    """One payment by id."""
    with domain_errors(), connection_scope() as connection:
        return SimulatedPaymentGateway().get(connection, payment_id).to_dict()


@router.post("/payments/{payment_id}/verify")
def verify_payment(payment_id: str) -> dict[str, Any]:
    """Reconcile a settled payment against its decision, designation and audit chain."""
    with domain_errors(), transaction() as connection:
        return SimulatedPaymentGateway().verify(connection, payment_id)


# ---------------------------------------------------------------------------
# Policy semantics and demo lifecycle
# ---------------------------------------------------------------------------
@router.get("/policies/{policy_id}/semantics")
def policy_semantics(policy_id: str) -> dict[str, Any]:
    """Certified semantic values for a policy, evaluated at its latest claim event."""
    with domain_errors(), connection_scope() as connection:
        return service.policy_semantics(connection, policy_id)


@router.post("/demo/reset")
def demo_reset() -> dict[str, Any]:
    """Rebuild the sample database and catalog.

    Present so the demonstration is reproducible. A deployment that exposes this
    router publicly should remove it: it drops every claims row.
    """
    with domain_errors():
        return service.to_jsonable(reset_and_seed())


@router.post("/demo/bootstrap")
def demo_bootstrap() -> dict[str, Any]:
    """Create the schema and catalogs without dropping existing data."""
    with domain_errors():
        return service.to_jsonable(bootstrap(seed=True))
