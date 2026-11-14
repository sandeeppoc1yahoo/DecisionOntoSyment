# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Relational schema for the life insurance claims domain.

**Why these tables are separate from the Auto-Ontology catalog.** The platform's
``catalog_*`` / ``term`` / ``sql_attribute`` tables describe *metadata*: what
columns exist in a customer database and what business term they mean. The
tables here hold *facts about people and money*: who is insured, what the policy
pays, which evidence was verified. Folding policy facts into the catalog would
make the ontology describe its own system of record, which is the one thing a
semantic layer must not do.

**What belongs where.**

============================  ==============================================
Question                      Layer / storage
============================  ==============================================
What is factually true now?    These relational tables.
What does a term mean?         ``insurance_semantic_definition`` (certified
                               definition) + ``semantic/engine.py`` (the
                               calculation that implements it).
What kinds of things exist     ``insurance_ontology_entity`` /
and how may they relate?       ``insurance_ontology_relationship``, populated
                               from ``ontology/vocabulary.py`` (T-Box).
Which things actually exist    ``insurance_kg_node`` / ``insurance_kg_edge``,
and how are they connected?    derived from the relational rows (A-Box).
Why was this claim approved?   ``insurance_decision_result`` plus
                               ``insurance_claim_approval_history`` and
                               ``insurance_audit_event``.
============================  ==============================================

**No duplicated facts.** A person's name lives once, in ``insurance_party``;
``insurance_policyholder``, ``insurance_insured_person`` and
``insurance_beneficiary`` are *roles* over that party. An allocation lives on
``insurance_policy_beneficiary`` because it is a property of the designation,
not of the person: the same spouse can be a 100% primary on one policy and a
contingent on another.

**Idempotent writes.** Every derived table (ontology, graph, decision) is
rewritten for its scope rather than appended to: rebuilding a claim's graph
deletes that claim's old nodes and edges first, so a re-run cannot leave two
versions of the same relationship and silently double the evidence.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import JSON

from auto_ontology.insurance.types import Money, Percent

#: Stable constraint names so a migrated Postgres deployment matches a fresh
#: SQLite build and Alembic can find what it is asked to drop.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

INSURANCE_METADATA = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------------------
# Parties and the roles a party can hold
# ---------------------------------------------------------------------------
insurance_party = Table(
    "insurance_party",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("party_type", Text, nullable=False),
    Column("first_name", Text, nullable=False),
    Column("middle_name", Text, nullable=True),
    Column("last_name", Text, nullable=False),
    Column("date_of_birth", DateTime, nullable=True),
    Column("gender", Text, nullable=True),
    Column("email", Text, nullable=True),
    Column("phone", Text, nullable=True),
    Column("tax_id_last4", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint("party_type IN ('PERSON', 'ORGANISATION')", name="party_type"),
    Index("ix_insurance_party_last_name", "last_name"),
)

insurance_policyholder = Table(
    "insurance_policyholder",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "party_id",
        Text,
        ForeignKey("insurance_party.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("customer_number", Text, nullable=False, unique=True),
    Column("created_at", DateTime, nullable=False),
)

insurance_insured_person = Table(
    "insurance_insured_person",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "party_id",
        Text,
        ForeignKey("insurance_party.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("occupation", Text, nullable=True),
    Column("smoker_status", Text, nullable=True),
    Column("height_cm", Integer, nullable=True),
    Column("weight_kg", Integer, nullable=True),
    Column("created_at", DateTime, nullable=False),
)


# ---------------------------------------------------------------------------
# Policy, designations, loans and restrictions
# ---------------------------------------------------------------------------
insurance_policy = Table(
    "insurance_policy",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("policy_number", Text, nullable=False, unique=True),
    Column(
        "policyholder_id",
        Text,
        ForeignKey("insurance_policyholder.id"),
        nullable=False,
    ),
    Column(
        "insured_person_id",
        Text,
        ForeignKey("insurance_insured_person.id"),
        nullable=False,
    ),
    Column("product_code", Text, nullable=False),
    Column("product_name", Text, nullable=False),
    Column("effective_date", DateTime, nullable=False),
    Column("expiry_date", DateTime, nullable=False),
    Column("face_amount", Money, nullable=False),
    Column("premium_amount", Money, nullable=False),
    Column("premium_mode", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("issue_state", Text, nullable=True),
    #: Two years by contract. Stored per policy because a policy's own schedule
    #: governs, not a global default.
    Column("contestability_days", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint(
        "status IN ('IN_FORCE', 'LAPSED', 'SURRENDERED', 'CLAIMED', 'TERMINATED')",
        name="status",
    ),
    Index("ix_insurance_policy_insured_person_id", "insured_person_id"),
)

insurance_beneficiary = Table(
    "insurance_beneficiary",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "party_id",
        Text,
        ForeignKey("insurance_party.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("relationship_to_insured", Text, nullable=False),
    Column("beneficiary_class", Text, nullable=False),
    Column("verification_status", Text, nullable=False),
    Column("verified_at", DateTime, nullable=True),
    Column("verified_by", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint(
        "beneficiary_class IN ('PRIMARY', 'CONTINGENT')", name="beneficiary_class"
    ),
    CheckConstraint(
        "verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')",
        name="verification_status",
    ),
    Index("ix_insurance_beneficiary_party_id", "party_id"),
)

insurance_policy_beneficiary = Table(
    "insurance_policy_beneficiary",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "policy_id",
        Text,
        ForeignKey("insurance_policy.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "beneficiary_id",
        Text,
        ForeignKey("insurance_beneficiary.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("allocation_percent", Percent, nullable=False),
    Column("designation_order", Integer, nullable=False),
    Column("designation_type", Text, nullable=False),
    Column("effective_from", DateTime, nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("policy_id", "beneficiary_id", name="policy_beneficiary"),
    CheckConstraint(
        "designation_type IN ('PRIMARY', 'CONTINGENT')", name="designation_type"
    ),
)

insurance_policy_loan = Table(
    "insurance_policy_loan",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "policy_id",
        Text,
        ForeignKey("insurance_policy.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("loan_number", Text, nullable=False, unique=True),
    Column("principal_amount", Money, nullable=False),
    Column("outstanding_balance", Money, nullable=False),
    Column("interest_rate_percent", Percent, nullable=False),
    Column("taken_date", DateTime, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint(
        "status IN ('OUTSTANDING', 'REPAID', 'CHARGED_OFF')", name="status"
    ),
    Index("ix_insurance_policy_loan_policy_id", "policy_id"),
)

insurance_policy_exclusion = Table(
    "insurance_policy_exclusion",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "policy_id",
        Text,
        ForeignKey("insurance_policy.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("exclusion_code", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    #: The ontology class of death this exclusion removes from cover, e.g.
    #: ``lio:SelfInflictedDeath``. A code, not prose, so matching is inference
    #: rather than keyword search.
    Column("excludes_cause_class", Text, nullable=True),
    Column("contestability_only", Boolean, nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Index("ix_insurance_policy_exclusion_policy_id", "policy_id"),
)

insurance_legal_restriction = Table(
    "insurance_legal_restriction",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "policy_id",
        Text,
        ForeignKey("insurance_policy.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "claim_id",
        Text,
        ForeignKey("insurance_claim.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("restriction_type", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("amount", Money, nullable=True),
    Column("status", Text, nullable=False),
    Column("imposed_at", DateTime, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint(
        "restriction_type IN ('LIEN', 'TRUST', 'COURT_ORDER', 'GARNISHMENT', "
        "'FRAUD_HOLD', 'CONTESTED_CLAIM')",
        name="restriction_type",
    ),
    CheckConstraint("status IN ('ACTIVE', 'RELEASED')", name="status"),
    Index("ix_insurance_legal_restriction_policy_id", "policy_id"),
)


# ---------------------------------------------------------------------------
# Cause of death (reference data) and claims
# ---------------------------------------------------------------------------
insurance_cause_of_death = Table(
    "insurance_cause_of_death",
    INSURANCE_METADATA,
    Column("code", Text, primary_key=True),
    Column("description", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("ontology_class_iri", Text, nullable=False),
    Column("is_self_inflicted", Boolean, nullable=False),
    Column("icd10_chapter", Text, nullable=True),
    CheckConstraint(
        "category IN ('NATURAL', 'ACCIDENTAL', 'SELF_INFLICTED', 'UNDETERMINED')",
        name="category",
    ),
)

insurance_death_certificate = Table(
    "insurance_death_certificate",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("certificate_number", Text, nullable=False, unique=True),
    Column(
        "insured_person_id",
        Text,
        ForeignKey("insurance_insured_person.id"),
        nullable=False,
    ),
    Column("date_of_death", DateTime, nullable=False),
    Column("place_of_death", Text, nullable=True),
    Column(
        "cause_code", Text, ForeignKey("insurance_cause_of_death.code"), nullable=False
    ),
    Column("manner_of_death", Text, nullable=False),
    Column("certifying_physician", Text, nullable=False),
    Column("issued_date", DateTime, nullable=False),
    Column("document_uri", Text, nullable=True),
    Column("document_hash", Text, nullable=False),
    Column("verification_status", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint(
        "verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')",
        name="verification_status",
    ),
    Index("ix_insurance_death_certificate_insured_person_id", "insured_person_id"),
)

insurance_claim = Table(
    "insurance_claim",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("claim_number", Text, nullable=False, unique=True),
    Column(
        "policy_id",
        Text,
        ForeignKey("insurance_policy.id"),
        nullable=False,
    ),
    Column(
        "insured_person_id",
        Text,
        ForeignKey("insurance_insured_person.id"),
        nullable=False,
    ),
    Column(
        "claimant_party_id",
        Text,
        ForeignKey("insurance_party.id"),
        nullable=False,
    ),
    Column(
        "death_certificate_id",
        Text,
        ForeignKey("insurance_death_certificate.id"),
        nullable=True,
    ),
    Column("claim_type", Text, nullable=False),
    Column("date_of_death", DateTime, nullable=False),
    Column(
        "cause_code", Text, ForeignKey("insurance_cause_of_death.code"), nullable=False
    ),
    Column("status", Text, nullable=False),
    Column("channel", Text, nullable=False),
    Column("submitted_at", DateTime, nullable=False),
    Column("notes", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint(
        "status IN ('SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'DENIED', 'PAID', "
        "'WITHDRAWN')",
        name="status",
    ),
    Index("ix_insurance_claim_policy_id", "policy_id"),
    Index("ix_insurance_claim_insured_person_id", "insured_person_id"),
)

insurance_claim_evidence = Table(
    "insurance_claim_evidence",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "claim_id",
        Text,
        ForeignKey("insurance_claim.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("evidence_type", Text, nullable=False),
    Column("document_ref", Text, nullable=False),
    Column("document_hash", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("received_at", DateTime, nullable=False),
    Column("verified_at", DateTime, nullable=True),
    Column("verified_by", Text, nullable=True),
    Column("notes", Text, nullable=True),
    CheckConstraint(
        "status IN ('PENDING', 'VERIFIED', 'REJECTED', 'WAIVED')", name="status"
    ),
    Index("ix_insurance_claim_evidence_claim_id", "claim_id"),
    UniqueConstraint("claim_id", "evidence_type", name="claim_evidence_type"),
)


# ---------------------------------------------------------------------------
# Semantic layer: certified definitions
# ---------------------------------------------------------------------------
insurance_semantic_definition = Table(
    "insurance_semantic_definition",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("description", Text, nullable=False),
    #: FORMULA (computed), PARAMETER (contract value read from a row),
    #: DERIVED (formula over other definitions). The kind tells a reader
    #: whether the number is arithmetic or a recorded fact.
    Column("definition_kind", Text, nullable=False),
    Column("formula", Text, nullable=False),
    Column("unit", Text, nullable=False),
    Column("depends_on", JSON, nullable=False),
    Column("version", Text, nullable=False),
    Column("owner", Text, nullable=False),
    Column("is_certified", Boolean, nullable=False),
    Column("certified_by", Text, nullable=True),
    Column("effective_from", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    CheckConstraint(
        "definition_kind IN ('FORMULA', 'PARAMETER', 'DERIVED', 'CATEGORICAL')",
        name="definition_kind",
    ),
)


# ---------------------------------------------------------------------------
# Ontology: T-Box
# ---------------------------------------------------------------------------
insurance_ontology_entity = Table(
    "insurance_ontology_entity",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("iri", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("entity_kind", Text, nullable=False),
    Column("parent_iri", Text, nullable=True),
    Column("description", Text, nullable=False),
    Column("constraints", JSON, nullable=False),
    Column("version", Text, nullable=False),
    Column("is_certified", Boolean, nullable=False),
    CheckConstraint("entity_kind IN ('CLASS', 'PROPERTY', 'INDIVIDUAL')", name="kind"),
)

insurance_ontology_relationship = Table(
    "insurance_ontology_relationship",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("iri", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("domain_iri", Text, nullable=False),
    Column("range_iri", Text, nullable=False),
    Column("cardinality", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("is_transitive", Boolean, nullable=False),
    Column("is_symmetric", Boolean, nullable=False),
    Column("version", Text, nullable=False),
    Column("is_certified", Boolean, nullable=False),
)


# ---------------------------------------------------------------------------
# Knowledge graph: A-Box for a single claim's context
# ---------------------------------------------------------------------------
insurance_kg_node = Table(
    "insurance_kg_node",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "claim_id",
        Text,
        ForeignKey("insurance_claim.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("entity_class_iri", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("source_table", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("properties", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("claim_id", "id", name="claim_node"),
    Index("ix_insurance_kg_node_claim_id", "claim_id"),
    Index("ix_insurance_kg_node_source_id", "source_id"),
)

insurance_kg_edge = Table(
    "insurance_kg_edge",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "claim_id",
        Text,
        ForeignKey("insurance_claim.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("from_node_id", Text, nullable=False),
    Column("to_node_id", Text, nullable=False),
    Column("relation_iri", Text, nullable=False),
    Column("properties", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Index("ix_insurance_kg_edge_claim_id", "claim_id"),
    Index("ix_insurance_kg_edge_from_node_id", "from_node_id"),
)


# ---------------------------------------------------------------------------
# Decision rules, results, approvals, payments, audit
# ---------------------------------------------------------------------------
insurance_decision_rule = Table(
    "insurance_decision_rule",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("severity", Text, nullable=False),
    #: What a failed evaluation means: DENY (contract excludes), REFER
    #: (a human must look), or INFORM (affects the amount, not eligibility).
    Column("on_fail_action", Text, nullable=False),
    Column("expression", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("priority", Integer, nullable=False),
    Column("is_active", Boolean, nullable=False),
    CheckConstraint(
        "category IN ('POLICY', 'SEMANTIC', 'ONTOLOGY', 'KNOWLEDGE_GRAPH', "
        "'EVIDENCE', 'LIMIT')",
        name="category",
    ),
    CheckConstraint(
        "on_fail_action IN ('DENY', 'REFER', 'INFORM')", name="on_fail_action"
    ),
)

insurance_decision_result = Table(
    "insurance_decision_result",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "claim_id",
        Text,
        ForeignKey("insurance_claim.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("policy_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("is_current", Boolean, nullable=False),
    Column("net_payout_amount", Money, nullable=False),
    Column("currency", Text, nullable=False),
    Column("confidence_score", Float, nullable=False),
    Column("rules_applied", JSON, nullable=False),
    Column("semantic_calculations", JSON, nullable=False),
    Column("ontology_validation", JSON, nullable=False),
    Column("kg_evidence", JSON, nullable=False),
    Column("explanation", Text, nullable=False),
    Column("decision_path", Text, nullable=False),
    Column("audit_reference", Text, nullable=False),
    Column("engine_version", Text, nullable=False),
    Column("decided_at", DateTime, nullable=False),
    Column("decided_by", Text, nullable=False),
    CheckConstraint(
        "status IN ('AUTO_APPROVED', 'MANUAL_REVIEW', 'DENIED')", name="status"
    ),
    Index("ix_insurance_decision_result_claim_id", "claim_id"),
)

insurance_claim_approval_history = Table(
    "insurance_claim_approval_history",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "claim_id",
        Text,
        ForeignKey("insurance_claim.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("decision_result_id", Text, nullable=True),
    Column("from_status", Text, nullable=False),
    Column("to_status", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("actor_type", Text, nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("approval_level", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Index("ix_insurance_claim_approval_history_claim_id", "claim_id"),
)

insurance_payment_transaction = Table(
    "insurance_payment_transaction",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "claim_id",
        Text,
        ForeignKey("insurance_claim.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "decision_result_id",
        Text,
        ForeignKey("insurance_decision_result.id"),
        nullable=False,
    ),
    Column(
        "beneficiary_id",
        Text,
        ForeignKey("insurance_beneficiary.id"),
        nullable=False,
    ),
    Column("amount", Money, nullable=False),
    Column("currency", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("authorization_reference", Text, nullable=False),
    Column("provider_reference", Text, nullable=True),
    Column("account_last4", Text, nullable=False),
    Column("routing_last4", Text, nullable=False),
    Column("requires_human_approval", Boolean, nullable=False),
    Column("approved_by", Text, nullable=True),
    Column("approved_at", DateTime, nullable=True),
    Column("settled_at", DateTime, nullable=True),
    Column("verification_status", Text, nullable=False),
    Column("failure_reason", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint(
        "status IN ('AUTHORIZED', 'SETTLED', 'FAILED', 'REVERSED')", name="status"
    ),
    Index("ix_insurance_payment_transaction_claim_id", "claim_id"),
)

insurance_audit_event = Table(
    "insurance_audit_event",
    INSURANCE_METADATA,
    Column("id", Text, primary_key=True),
    Column("occurred_at", DateTime, nullable=False),
    Column("actor_type", Text, nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("correlation_id", Text, nullable=False),
    Column("payload", JSON, nullable=False),
    #: Hash chaining: each event carries the previous event's hash for the same
    #: correlation id, so deleting or editing one event breaks every later link.
    Column("previous_hash", Text, nullable=True),
    Column("event_hash", Text, nullable=False),
    Index("ix_insurance_audit_event_correlation_id", "correlation_id"),
    Index("ix_insurance_audit_event_entity_id", "entity_id"),
)
