# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build and persist the knowledge graph for one claim.

The builder is a *projection*, not a second source of truth. Every node is a row
that already exists in a relational table, and every edge is a foreign key or a
designation that already exists there. The graph adds three things the tables do
not: ontology typing, named relationships, and traversal - which is precisely
what a decision needs to answer "how is this claimant connected to this insured,
and what proves it".

Rebuild semantics: a claim's graph is deleted and rewritten as one unit. A
decision may be re-run; if the graph were appended to, a stale beneficiary or a
removed restriction would survive into the second adjudication.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, delete, select

from auto_ontology.insurance import repository
from auto_ontology.insurance.db import new_id, now_utc, rows
from auto_ontology.insurance.errors import ClaimNotFoundError, PolicyNotFoundError
from auto_ontology.insurance.kg.graph import GraphEdge, GraphNode, KnowledgeGraph
from auto_ontology.insurance.ontology.reasoner import classify_cause
from auto_ontology.insurance.ontology.vocabulary import LIO
from auto_ontology.insurance.schema import insurance_kg_edge, insurance_kg_node


def node_id(class_local: str, source_id: str) -> str:
    """Stable, readable node id: ``PrimaryBeneficiary:BEN-3001``."""
    return f"{class_local}:{source_id}"


def _edge_id(from_id: str, relation: str, to_id: str) -> str:
    """Deterministic edge id so a rebuild does not churn identity."""
    return f"{from_id}--{relation}-->{to_id}"


def _relationship_class(relationship: str) -> str | None:
    """Ontology role class for a beneficiary's stated relationship."""
    mapping = {
        "SPOUSE": "lio:Spouse",
        "CHILD": "lio:Child",
    }
    return mapping.get(relationship.upper())


def build_claim_graph(connection: Connection, claim_id: str) -> KnowledgeGraph:
    """Materialise the claim's entities and relationships from relational rows."""
    claim = repository.get_claim(connection, claim_id)
    if claim is None:
        raise ClaimNotFoundError(claim_id)
    policy = repository.get_policy(connection, claim["policy_id"])
    if policy is None:
        raise PolicyNotFoundError(claim["policy_id"])

    insured = repository.get_insured(connection, policy["insured_person_id"])
    policyholder = repository.get_policyholder(connection, policy["policyholder_id"])
    beneficiaries = repository.get_beneficiaries(connection, policy["id"])
    loans = repository.get_loans(connection, policy["id"])
    exclusions = repository.get_exclusions(connection, policy["id"])
    restrictions = repository.get_restrictions(connection, policy["id"])
    evidence = repository.get_evidence(connection, claim_id)
    certificate = (
        repository.get_certificate(connection, claim["death_certificate_id"])
        if claim.get("death_certificate_id")
        else None
    )
    competing = repository.get_competing_claims(
        connection, claim_id=claim_id, policy_id=policy["id"]
    )
    classification = classify_cause(claim["cause_code"])

    graph = KnowledgeGraph(claim_id=claim_id, nodes=[], edges=[])

    claim_node_id = node_id("InsuranceClaim", claim["id"])
    policy_node_id = node_id("LifeInsurancePolicy", policy["id"])
    insured_node_id = node_id("InsuredPerson", policy["insured_person_id"])

    graph.add_node(
        GraphNode(
            id=claim_node_id,
            entity_class_iri=LIO + "InsuranceClaim",
            label=claim["claim_number"],
            source_table="insurance_claim",
            source_id=claim["id"],
            properties={
                "status": claim["status"],
                "claim_type": claim["claim_type"],
                "date_of_death": claim["date_of_death"].isoformat(),
                "cause_code": claim["cause_code"],
                "submitted_at": claim["submitted_at"].isoformat(),
            },
        )
    )
    graph.add_node(
        GraphNode(
            id=policy_node_id,
            entity_class_iri=LIO + "LifeInsurancePolicy",
            label=policy["policy_number"],
            source_table="insurance_policy",
            source_id=policy["id"],
            properties={
                "product": policy["product_name"],
                "status": policy["status"],
                "effective_date": policy["effective_date"].isoformat(),
                "expiry_date": policy["expiry_date"].isoformat(),
                "face_amount": str(policy["face_amount"]),
                "contestability_days": policy["contestability_days"],
            },
        )
    )
    if policyholder is not None:
        graph.add_node(
            GraphNode(
                id=node_id("Policyholder", policyholder["policyholder_id"]),
                entity_class_iri=LIO + "Policyholder",
                label=repository.full_name(policyholder),
                source_table="insurance_policyholder",
                source_id=policyholder["policyholder_id"],
                properties={"customer_number": policyholder["customer_number"]},
            )
        )
    if insured is not None:
        graph.add_node(
            GraphNode(
                id=insured_node_id,
                entity_class_iri=LIO + "InsuredPerson",
                label=repository.full_name(insured),
                source_table="insurance_insured_person",
                source_id=insured["insured_person_id"],
                properties={
                    "date_of_birth": (
                        insured["date_of_birth"].date().isoformat()
                        if insured.get("date_of_birth")
                        else None
                    ),
                    "occupation": insured.get("occupation"),
                },
            )
        )

    for beneficiary in beneficiaries:
        primary = beneficiary["designation_type"] == "PRIMARY"
        class_iri = LIO + ("PrimaryBeneficiary" if primary else "ContingentBeneficiary")
        types = [class_iri]
        relationship_class = _relationship_class(beneficiary["relationship_to_insured"])
        if relationship_class:
            types.append(relationship_class)
        graph.add_node(
            GraphNode(
                id=node_id(class_iri.removeprefix(LIO), beneficiary["beneficiary_id"]),
                entity_class_iri=class_iri,
                label=beneficiary.get("full_name") or beneficiary["beneficiary_id"],
                source_table="insurance_beneficiary",
                source_id=beneficiary["beneficiary_id"],
                properties={
                    "ontology_types": types,
                    "relationship_to_insured": beneficiary["relationship_to_insured"],
                    "allocation_percent": str(beneficiary["allocation_percent"]),
                    "verification_status": beneficiary["verification_status"],
                    "policy_id": policy["id"],
                },
            )
        )

    claimant_id = node_id("Claimant", claim["claimant_party_id"])
    claimant_party = repository.get_party(connection, claim["claimant_party_id"])
    graph.add_node(
        GraphNode(
            id=claimant_id,
            entity_class_iri=LIO + "Claimant",
            label=repository.full_name(claimant_party),
            source_table="insurance_party",
            source_id=claim["claimant_party_id"],
            properties={"party_id": claim["claimant_party_id"]},
        )
    )

    cause_node_id = node_id(classification.class_label, classification.code)
    graph.add_node(
        GraphNode(
            id=cause_node_id,
            entity_class_iri=classification.class_iri,
            label=f"{classification.code} {classification.description}",
            source_table="insurance_cause_of_death",
            source_id=classification.code,
            properties={
                "category": classification.category,
                "inference": classification.inference,
                "ancestors": list(classification.ancestors),
            },
        )
    )

    certificate_node_id: str | None = None
    if certificate is not None:
        certificate_node_id = node_id("DeathCertificate", certificate["id"])
        graph.add_node(
            GraphNode(
                id=certificate_node_id,
                entity_class_iri=LIO + "DeathCertificate",
                label=certificate["certificate_number"],
                source_table="insurance_death_certificate",
                source_id=certificate["id"],
                properties={
                    "date_of_death": certificate["date_of_death"].isoformat(),
                    "cause_code": certificate["cause_code"],
                    "verification_status": certificate["verification_status"],
                    "certifying_physician": certificate["certifying_physician"],
                    "document_hash": certificate["document_hash"],
                },
            )
        )

    for item in evidence:
        graph.add_node(
            GraphNode(
                id=node_id("ClaimEvidence", item["id"]),
                entity_class_iri=LIO + "ClaimEvidence",
                label=f"{item['evidence_type']} ({item['status']})",
                source_table="insurance_claim_evidence",
                source_id=item["id"],
                properties={
                    "evidence_type": item["evidence_type"],
                    "status": item["status"],
                    "document_ref": item["document_ref"],
                    "verified_by": item.get("verified_by"),
                },
            )
        )

    for loan in loans:
        graph.add_node(
            GraphNode(
                id=node_id("PolicyLoan", loan["id"]),
                entity_class_iri=LIO + "PolicyLoan",
                label=loan["loan_number"],
                source_table="insurance_policy_loan",
                source_id=loan["id"],
                properties={
                    "status": loan["status"],
                    "outstanding_balance": str(loan["outstanding_balance"]),
                },
            )
        )

    for exclusion in exclusions:
        graph.add_node(
            GraphNode(
                id=node_id("PolicyExclusion", exclusion["id"]),
                entity_class_iri=LIO + "PolicyExclusion",
                label=exclusion["exclusion_code"],
                source_table="insurance_policy_exclusion",
                source_id=exclusion["id"],
                properties={
                    "name": exclusion["name"],
                    "excludes_cause_class": exclusion["excludes_cause_class"],
                    "contestability_only": exclusion["contestability_only"],
                    "is_active": exclusion["is_active"],
                },
            )
        )

    for restriction in restrictions:
        graph.add_node(
            GraphNode(
                id=node_id("LegalRestriction", restriction["id"]),
                entity_class_iri=LIO + "LegalRestriction",
                label=f"{restriction['restriction_type']} ({restriction['status']})",
                source_table="insurance_legal_restriction",
                source_id=restriction["id"],
                properties={
                    "restriction_type": restriction["restriction_type"],
                    "description": restriction["description"],
                    "status": restriction["status"],
                },
            )
        )

    for other in competing:
        graph.add_node(
            GraphNode(
                id=node_id("InsuranceClaim", other["id"]),
                entity_class_iri=LIO + "InsuranceClaim",
                label=other["claim_number"],
                source_table="insurance_claim",
                source_id=other["id"],
                properties={"status": other["status"], "competing": True},
            )
        )

    def link(from_id: str, relation: str, to_id: str, **properties: Any) -> None:
        graph.add_edge(
            GraphEdge(
                id=_edge_id(from_id, relation, to_id),
                from_node_id=from_id,
                to_node_id=to_id,
                relation_iri=LIO + relation,
                properties=properties,
            )
        )

    if policyholder is not None:
        link(
            node_id("Policyholder", policyholder["policyholder_id"]),
            "ownsPolicy",
            policy_node_id,
        )
    if insured is not None:
        link(insured_node_id, "coveredBy", policy_node_id)

    for beneficiary in beneficiaries:
        class_local = (
            "PrimaryBeneficiary"
            if beneficiary["designation_type"] == "PRIMARY"
            else "ContingentBeneficiary"
        )
        beneficiary_node = node_id(class_local, beneficiary["beneficiary_id"])
        link(policy_node_id, "hasBeneficiary", beneficiary_node)
        link(beneficiary_node, "beneficiaryOf", policy_node_id)
        link(beneficiary_node, "hasRelationshipToInsured", insured_node_id)
        if _relationship_class(beneficiary["relationship_to_insured"]) == "lio:Spouse":
            link(beneficiary_node, "spouseOf", insured_node_id)
        if beneficiary["party_id"] == claim["claimant_party_id"]:
            # One human, two roles. An identity edge keeps the graph honest
            # without duplicating the person.
            link(claimant_id, "sameAs", beneficiary_node)

    link(policy_node_id, "hasClaim", claim_node_id)
    link(claim_node_id, "submittedBy", claimant_id)
    link(claim_node_id, "relatesToInsured", insured_node_id)
    for item in evidence:
        link(claim_node_id, "hasEvidence", node_id("ClaimEvidence", item["id"]))
    if certificate_node_id:
        link(claim_node_id, "evidencedByCertificate", certificate_node_id)
        link(certificate_node_id, "certifiesDeathOf", insured_node_id)
        link(certificate_node_id, "hasCauseOfDeath", cause_node_id)
    for loan in loans:
        link(policy_node_id, "hasLoan", node_id("PolicyLoan", loan["id"]))
    for exclusion in exclusions:
        link(
            policy_node_id,
            "hasExclusion",
            node_id("PolicyExclusion", exclusion["id"]),
        )
    for restriction in restrictions:
        link(
            policy_node_id,
            "hasRestriction",
            node_id("LegalRestriction", restriction["id"]),
        )
    for other in competing:
        link(policy_node_id, "hasClaim", node_id("InsuranceClaim", other["id"]))

    return graph


def persist_graph(connection: Connection, graph: KnowledgeGraph) -> dict[str, int]:
    """Replace the claim's stored graph with this projection."""
    connection.execute(
        delete(insurance_kg_node).where(insurance_kg_node.c.claim_id == graph.claim_id)
    )
    connection.execute(
        delete(insurance_kg_edge).where(insurance_kg_edge.c.claim_id == graph.claim_id)
    )
    timestamp = now_utc()
    for node in graph.nodes:
        connection.execute(
            insurance_kg_node.insert().values(
                id=node.id,
                claim_id=graph.claim_id,
                entity_class_iri=node.entity_class_iri,
                label=node.label,
                source_table=node.source_table,
                source_id=node.source_id,
                properties=node.properties,
                created_at=timestamp,
            )
        )
    for edge in graph.edges:
        connection.execute(
            insurance_kg_edge.insert().values(
                id=new_id("KGE"),
                claim_id=graph.claim_id,
                from_node_id=edge.from_node_id,
                to_node_id=edge.to_node_id,
                relation_iri=edge.relation_iri,
                properties=edge.properties,
                created_at=timestamp,
            )
        )
    return {"nodes": len(graph.nodes), "edges": len(graph.edges)}


def load_claim_graph(connection: Connection, claim_id: str) -> KnowledgeGraph:
    """Read the stored projection. Assumes a build has run for this claim."""
    node_rows = rows(
        connection,
        select(insurance_kg_node).where(insurance_kg_node.c.claim_id == claim_id),
    )
    edge_rows = rows(
        connection,
        select(insurance_kg_edge).where(insurance_kg_edge.c.claim_id == claim_id),
    )
    return KnowledgeGraph(
        claim_id=claim_id,
        nodes=[
            GraphNode(
                id=row["id"],
                entity_class_iri=row["entity_class_iri"],
                label=row["label"],
                source_table=row["source_table"],
                source_id=row["source_id"],
                properties=row["properties"] or {},
            )
            for row in node_rows
        ],
        edges=[
            GraphEdge(
                id=row["id"],
                from_node_id=row["from_node_id"],
                to_node_id=row["to_node_id"],
                relation_iri=row["relation_iri"],
                properties=row["properties"] or {},
            )
            for row in edge_rows
        ],
    )
