# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Assembly of everything one adjudication is allowed to see.

The context is the contract between the three analytical layers and the decision
engine. A rule receives this object and nothing else: no database handle, no
HTTP client, no model. That is what makes a decision reproducible from its
stored payload - the payload *is* the context, minus the text fields a reviewer
does not need.

Note the sequencing. The semantic layer runs first because the ontology and the
graph both need its calculations (contestability decides whether a
contestability-only exclusion applies). The graph is built from the relational
facts, then validated against the ontology, and only then is the ontology's
constraint validation run - so "is there a spouse designation" asks a graph
question, not a table question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection

from auto_ontology.insurance import repository
from auto_ontology.insurance.config import InsuranceSettings, get_settings
from auto_ontology.insurance.errors import ClaimNotFoundError, PolicyNotFoundError
from auto_ontology.insurance.kg.builder import (
    build_claim_graph,
    node_id,
    persist_graph,
)
from auto_ontology.insurance.kg.graph import GraphPath, KnowledgeGraph
from auto_ontology.insurance.ontology.reasoner import (
    CauseClassification,
    OntologyValidation,
    classify_cause,
    matched_exclusions,
    validate_claim,
)
from auto_ontology.insurance.semantic.engine import SemanticEngine, SemanticResult


@dataclass
class DecisionContext:
    """Every fact, calculation and inference available to the rule set."""

    settings: InsuranceSettings
    claim: dict[str, Any]
    policy: dict[str, Any]
    insured: dict[str, Any] | None
    certificate: dict[str, Any] | None
    classification: CauseClassification
    beneficiary_designations: list[dict[str, Any]]
    loans: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    restrictions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    competing_claims: list[dict[str, Any]]
    semantic: SemanticResult
    ontology: list[OntologyValidation]
    graph: KnowledgeGraph
    graph_violations: list[dict[str, Any]] = field(default_factory=list)
    matched_exclusions: list[dict[str, Any]] = field(default_factory=list)
    evidence_chain: GraphPath | None = None
    primary_beneficiary_node: str | None = None

    # -- convenience accessors used by rules -------------------------------
    @property
    def verified_evidence_types(self) -> set[str]:
        return {
            item["evidence_type"]
            for item in self.evidence
            if item["status"] == "VERIFIED"
        }

    @property
    def missing_evidence(self) -> list[str]:
        return sorted(
            set(self.settings.required_evidence_types) - self.verified_evidence_types
        )

    def ontology_status(self, code: str) -> str:
        for result in self.ontology:
            if result.code == code:
                return result.status
        return "UNKNOWN"

    def ontology_result(self, code: str) -> OntologyValidation | None:
        for result in self.ontology:
            if result.code == code:
                return result
        return None

    def semantic_bool(self, name: str) -> bool:
        return bool(self.semantic.get(name))

    def evidence_chain_description(self) -> str:
        return (
            self.evidence_chain.describe() if self.evidence_chain else "no chain found"
        )


def build_context(
    connection: Connection,
    claim_id: str,
    *,
    settings: InsuranceSettings | None = None,
    persist: bool = True,
) -> DecisionContext:
    """Load facts, calculate, project the graph, and validate the ontology."""
    settings = settings or get_settings()
    claim = repository.get_claim(connection, claim_id)
    if claim is None:
        raise ClaimNotFoundError(claim_id)
    policy = repository.get_policy(connection, claim["policy_id"])
    if policy is None:
        raise PolicyNotFoundError(claim["policy_id"])

    semantic = SemanticEngine().evaluate(connection, claim_id)
    classification = classify_cause(claim["cause_code"])

    graph = build_claim_graph(connection, claim_id)
    if persist:
        persist_graph(connection, graph)

    insured = repository.get_insured(connection, policy["insured_person_id"])
    certificate = (
        repository.get_certificate(connection, claim["death_certificate_id"])
        if claim.get("death_certificate_id")
        else None
    )
    beneficiaries = repository.get_beneficiaries(connection, policy["id"])
    exclusions = repository.get_exclusions(connection, policy["id"])
    restrictions = repository.get_restrictions(connection, policy["id"])
    evidence = repository.get_evidence(connection, claim_id)
    competing = repository.get_competing_claims(
        connection, claim_id=claim_id, policy_id=policy["id"]
    )

    applicable = matched_exclusions(
        exclusions,
        classification,
        contestability_passed=bool(semantic.get("past_contestability")),
    )

    facts = {
        "claim": claim,
        "policy": policy,
        "certificate": certificate,
        "beneficiaries": beneficiaries,
        "exclusions": exclusions,
        "competing_claims": competing,
        "classification": classification,
        "contestability_passed": bool(semantic.get("past_contestability")),
    }
    ontology = validate_claim(facts)

    context = DecisionContext(
        settings=settings,
        claim=claim,
        policy=policy,
        insured=insured,
        certificate=certificate,
        classification=classification,
        beneficiary_designations=beneficiaries,
        loans=repository.get_loans(connection, policy["id"]),
        exclusions=exclusions,
        restrictions=restrictions,
        evidence=evidence,
        competing_claims=competing,
        semantic=semantic,
        ontology=ontology,
        graph=graph,
        graph_violations=graph.validate_relationships(),
        matched_exclusions=applicable,
    )

    primary_nodes = [
        node
        for node in graph.nodes_of_class("lio:PrimaryBeneficiary")
        if node.properties.get("verification_status") == "VERIFIED"
    ]
    insured_nodes = graph.nodes_of_class("lio:InsuredPerson")
    if primary_nodes:
        context.primary_beneficiary_node = primary_nodes[0].id
    if insured_nodes and primary_nodes:
        policy_node = node_id("LifeInsurancePolicy", policy["id"])
        insured_node = insured_nodes[0].id
        beneficiary_node = primary_nodes[0].id
        # Ask for the exact chain the fast-track rule depends on, not just any
        # connection: insured -> policy -> primary beneficiary.
        through_policy = [
            path
            for path in graph.find_paths(insured_node, beneficiary_node)
            if policy_node in {node.id for node in path.nodes}
        ]
        context.evidence_chain = through_policy[0] if through_policy else None

    return context
