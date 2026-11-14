# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for knowledge-graph construction and traversal."""

from __future__ import annotations

from auto_ontology.insurance.db import connection_scope, transaction
from auto_ontology.insurance.decision.context import build_context
from auto_ontology.insurance.kg.builder import (
    build_claim_graph,
    load_claim_graph,
    node_id,
    persist_graph,
)
from auto_ontology.insurance.ontology.vocabulary import PROPERTY_BY_IRI
from auto_ontology.insurance.seed import SCENARIO_A_CLAIM_ID, SCENARIO_B_CLAIM_ID


def test_graph_is_built_from_relational_facts(fresh_db: None) -> None:
    with connection_scope() as connection:
        graph = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)

    assert graph.node(node_id("InsuredPerson", "INS-4001")) is not None
    assert graph.node(node_id("LifeInsurancePolicy", "POL-1001")) is not None
    assert graph.node(node_id("PrimaryBeneficiary", "BEN-3001")) is not None
    assert graph.node(node_id("DeathCertificate", "DC-9001")) is not None
    assert graph.node(node_id("CardiovascularDeath", "I21.9")) is not None
    assert len(graph.nodes_of_class("lio:ClaimEvidence")) == 4


def test_required_relationship_chain(fresh_db: None) -> None:
    with connection_scope() as connection:
        graph = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)

    paths = graph.find_paths(
        node_id("InsuredPerson", "INS-4001"),
        node_id("PrimaryBeneficiary", "BEN-3001"),
    )
    assert paths, "insured should reach the primary beneficiary"
    shortest = paths[0]
    assert [edge.relation for edge in shortest.edges] == [
        "coveredBy",
        "hasBeneficiary",
    ]
    assert "Jane Marie Hayes" in shortest.describe()


def test_spouse_relationship_is_a_graph_edge(fresh_db: None) -> None:
    with connection_scope() as connection:
        graph = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)
    outgoing = graph.outgoing(
        node_id("PrimaryBeneficiary", "BEN-3001"), relation="spouseOf"
    )
    assert len(outgoing) == 1
    assert outgoing[0].to_node_id == node_id("InsuredPerson", "INS-4001")


def test_graph_agrees_with_the_ontology(fresh_db: None) -> None:
    with connection_scope() as connection:
        graph = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)
    assert graph.validate_relationships() == []


def test_every_edge_uses_a_declared_relation(fresh_db: None) -> None:
    with connection_scope() as connection:
        graph = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)
    for edge in graph.edges:
        assert edge.relation_iri in PROPERTY_BY_IRI


def test_graph_rebuild_is_idempotent(fresh_db: None) -> None:
    with transaction() as connection:
        graph = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)
        persist_graph(connection, graph)
        first = load_claim_graph(connection, SCENARIO_A_CLAIM_ID)

    with transaction() as connection:
        graph_again = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)
        persist_graph(connection, graph_again)
        second = load_claim_graph(connection, SCENARIO_A_CLAIM_ID)

    assert len(first.nodes) == len(second.nodes)
    assert len(first.edges) == len(second.edges)
    assert {node.id for node in first.nodes} == {node.id for node in second.nodes}


def test_restriction_is_in_the_second_claim_graph(fresh_db: None) -> None:
    with connection_scope() as connection:
        graph = build_claim_graph(connection, SCENARIO_B_CLAIM_ID)
    restrictions = graph.nodes_of_class("lio:LegalRestriction")
    assert len(restrictions) == 1
    assert restrictions[0].properties["restriction_type"] == "COURT_ORDER"
    assert graph.outgoing(
        node_id("LifeInsurancePolicy", "POL-1002"), relation="hasRestriction"
    )


def test_context_computes_the_verified_chain(fresh_db: None) -> None:
    with transaction() as connection:
        context = build_context(connection, SCENARIO_A_CLAIM_ID)
    assert context.evidence_chain is not None
    assert context.graph_violations == []
    assert context.primary_beneficiary_node == node_id("PrimaryBeneficiary", "BEN-3001")


def test_unknown_node_has_no_paths(fresh_db: None) -> None:
    with connection_scope() as connection:
        graph = build_claim_graph(connection, SCENARIO_A_CLAIM_ID)
    assert graph.find_paths("does-not-exist", "BEN-3001") == []
    assert not graph.path_exists("does-not-exist", "BEN-3001")
