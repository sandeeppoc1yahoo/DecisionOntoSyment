# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""In-memory knowledge graph with ontology-aware traversal.

The graph is small and claim-scoped (tens of nodes), so an adjacency-list graph
in Python is the right tool: it makes path queries, subgraph extraction and
"show me the evidence chain" cheap and dependency-free, and it is a faithful
projection of the persisted ``insurance_kg_node`` / ``insurance_kg_edge`` rows.

What the traversal is used for in claims processing:

* **Evidence chain.** ``InsuredPerson -> LifeInsurancePolicy ->
  PrimaryBeneficiary -> Spouse`` is not a slogan; it is a path query whose
  result is attached to the decision. If any link is missing, the fast-track
  rule sees it.
* **Consistency.** Domain/range checks against the ontology catch a certificate
  attached to the wrong person, or a beneficiary pointing at a policy that does
  not designate them.
* **Competing context.** Every other open claim on the policy is in the same
  graph, so "no competing claims" is a graph fact rather than a separate query
  the decision engine might forget.

Nodes carry their ontology type(s). A person can be both a
``PrimaryBeneficiary`` and a ``Spouse``; the multi-typing is stored in
``properties["ontology_types"]`` and validation accepts any declared type. That
avoids creating a duplicate "spouse node" for one human being.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from auto_ontology.insurance.ontology.reasoner import class_label
from auto_ontology.insurance.ontology.vocabulary import (
    LIO,
    PROPERTY_BY_IRI,
    class_parents,
)


@dataclass(frozen=True)
class GraphNode:
    id: str
    entity_class_iri: str
    label: str
    source_table: str
    source_id: str
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def types(self) -> list[str]:
        declared = self.properties.get("ontology_types")
        if isinstance(declared, list) and declared:
            return declared
        return [self.entity_class_iri]

    @property
    def class_name(self) -> str:
        return class_label(self.entity_class_iri)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_class": self.entity_class_iri,
            "entity_class_label": self.class_name,
            "label": self.label,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "properties": self.properties,
        }


@dataclass(frozen=True)
class GraphEdge:
    id: str
    from_node_id: str
    to_node_id: str
    relation_iri: str
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def relation(self) -> str:
        return self.relation_iri.removeprefix(LIO)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from": self.from_node_id,
            "to": self.to_node_id,
            "relation": self.relation,
            "relation_iri": self.relation_iri,
            "properties": self.properties,
        }


@dataclass(frozen=True)
class GraphPath:
    """A connected walk: n nodes joined by n-1 edges."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.nodes[0].id,
            "end": self.nodes[-1].id,
            "length": len(self.edges),
            "hops": [
                {
                    "from": edge.from_node_id,
                    "relation": edge.relation,
                    "to": edge.to_node_id,
                    "to_label": self.nodes[index + 1].label,
                }
                for index, edge in enumerate(self.edges)
            ],
        }

    def describe(self) -> str:
        """One-line evidence chain, using the graph's display labels."""
        if not self.nodes:
            return ""
        parts = [f"{self.nodes[0].class_name} {self.nodes[0].label}"]
        for edge, node in zip(self.edges, self.nodes[1:], strict=False):
            parts.append(f"--{edge.relation}--> {node.class_name} {node.label}")
        return " ".join(parts)


class KnowledgeGraph:
    """A claim-scoped property graph with adjacency traversal."""

    def __init__(self, claim_id: str, nodes: list[GraphNode], edges: list[GraphEdge]):
        self.claim_id = claim_id
        self._nodes: dict[str, GraphNode] = {node.id: node for node in nodes}
        self._edges: list[GraphEdge] = list(edges)
        self._out: dict[str, list[GraphEdge]] = {}
        self._in: dict[str, list[GraphEdge]] = {}
        for edge in self._edges:
            self._out.setdefault(edge.from_node_id, []).append(edge)
            self._in.setdefault(edge.to_node_id, []).append(edge)

    # -- construction ------------------------------------------------------
    def add_node(self, node: GraphNode) -> None:
        self._nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        self._edges.append(edge)
        self._out.setdefault(edge.from_node_id, []).append(edge)
        self._in.setdefault(edge.to_node_id, []).append(edge)

    @property
    def nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[GraphEdge]:
        return list(self._edges)

    # -- lookup ------------------------------------------------------------
    def node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def find(self, node_id: str) -> GraphNode:
        node = self._nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id!r} is not in claim {self.claim_id}'s graph")
        return node

    def nodes_of_class(
        self, class_iri: str, *, include_subclasses: bool = True
    ) -> list[GraphNode]:
        matches: list[GraphNode] = []
        for node in self._nodes.values():
            for node_type in node.types:
                if node_type == class_iri:
                    matches.append(node)
                    break
                if include_subclasses and _is_descendant(node_type, class_iri):
                    matches.append(node)
                    break
        return matches

    def outgoing(self, node_id: str, relation: str | None = None) -> list[GraphEdge]:
        edges = self._out.get(node_id, [])
        if relation is None:
            return list(edges)
        return [edge for edge in edges if edge.relation == relation]

    def incoming(self, node_id: str, relation: str | None = None) -> list[GraphEdge]:
        edges = self._in.get(node_id, [])
        if relation is None:
            return list(edges)
        return [edge for edge in edges if edge.relation == relation]

    def neighbors(self, node_id: str, relation: str | None = None) -> list[GraphNode]:
        found = [edge.to_node_id for edge in self.outgoing(node_id, relation)]
        found += [edge.from_node_id for edge in self.incoming(node_id, relation)]
        return [self._nodes[nid] for nid in dict.fromkeys(found) if nid in self._nodes]

    # -- traversal ---------------------------------------------------------
    def find_paths(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int = 6,
        limit: int = 10,
    ) -> list[GraphPath]:
        """Every simple path between two nodes, shortest first.

        Breadth-first with an explicit visited-on-path set: the graph has
        legitimate cycles (a spouse is the beneficiary of the policy that covers
        the spouse's spouse), and a naive DFS would loop. Paths are capped in
        length because the decision only trusts chains it can read.
        """
        if source_id not in self._nodes or target_id not in self._nodes:
            return []

        results: list[GraphPath] = []
        queue: deque[tuple[str, list[GraphNode], list[GraphEdge]]] = deque(
            [(source_id, [self._nodes[source_id]], [])]
        )
        while queue and len(results) < limit:
            current, path_nodes, path_edges = queue.popleft()
            if len(path_edges) >= max_depth:
                continue
            for edge in self._out.get(current, []):
                nxt = edge.to_node_id
                if nxt == target_id:
                    results.append(
                        GraphPath(
                            tuple([*path_nodes, self._nodes[nxt]]),
                            tuple([*path_edges, edge]),
                        )
                    )
                    if len(results) >= limit:
                        break
                    continue
                if any(node.id == nxt for node in path_nodes):
                    continue
                queue.append(
                    (nxt, [*path_nodes, self._nodes[nxt]], [*path_edges, edge])
                )
        results.sort(key=lambda path: len(path.edges))
        return results

    def path_exists(
        self, source_id: str, target_id: str, *, max_depth: int = 6
    ) -> bool:
        return bool(self.find_paths(source_id, target_id, max_depth=max_depth, limit=1))

    def edges_between(self, from_id: str, to_id: str) -> list[GraphEdge]:
        return [edge for edge in self._out.get(from_id, []) if edge.to_node_id == to_id]

    # -- ontology alignment ------------------------------------------------
    def validate_relationships(self) -> list[dict[str, Any]]:
        """Domain/range check every edge against the ontology.

        A violation is not automatically a claims denial - it is a modelling
        inconsistency that must be surfaced, because a graph that contradicts
        its ontology cannot be used as evidence. The decision engine turns any
        violation into a manual-review condition.
        """
        violations: list[dict[str, Any]] = []
        for edge in self._edges:
            prop = PROPERTY_BY_IRI.get(edge.relation_iri)
            if prop is None:
                violations.append(
                    {
                        "edge": edge.to_dict(),
                        "violation": "UNDECLARED_RELATION",
                        "detail": f"{edge.relation_iri} is not in the ontology",
                    }
                )
                continue
            source = self._nodes.get(edge.from_node_id)
            target = self._nodes.get(edge.to_node_id)
            if source is None or target is None:
                violations.append(
                    {
                        "edge": edge.to_dict(),
                        "violation": "DANGLING_EDGE",
                        "detail": "edge references a node outside the graph",
                    }
                )
                continue
            domain = LIO + prop.domain
            range_ = LIO + prop.range
            if not any(_is_descendant(node_type, domain) for node_type in source.types):
                violations.append(
                    {
                        "edge": edge.to_dict(),
                        "violation": "DOMAIN_MISMATCH",
                        "detail": (
                            f"{source.class_name} is not a {class_label(domain)}, "
                            f"which {prop.local} requires"
                        ),
                    }
                )
            if not any(_is_descendant(node_type, range_) for node_type in target.types):
                violations.append(
                    {
                        "edge": edge.to_dict(),
                        "violation": "RANGE_MISMATCH",
                        "detail": (
                            f"{target.class_name} is not a {class_label(range_)}, "
                            f"which {prop.local} requires"
                        ),
                    }
                )
        return violations

    # -- serialisation -----------------------------------------------------
    def statements(self, limit: int = 40) -> list[str]:
        """Human-readable triples for the decision's evidence section."""
        lines: list[str] = []
        for edge in self._edges[:limit]:
            source = self._nodes.get(edge.from_node_id)
            target = self._nodes.get(edge.to_node_id)
            if source is None or target is None:
                continue
            lines.append(
                f"{source.class_name}({source.label}) --{edge.relation}--> "
                f"{target.class_name}({target.label})"
            )
        return lines

    def to_dict(self) -> dict[str, Any]:
        class_counts: dict[str, int] = {}
        for node in self._nodes.values():
            class_counts[node.class_name] = class_counts.get(node.class_name, 0) + 1
        return {
            "claim_id": self.claim_id,
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "class_counts": class_counts,
            "nodes": [node.to_dict() for node in self._nodes.values()],
            "edges": [edge.to_dict() for edge in self._edges],
        }


def _is_descendant(candidate: str, ancestor: str) -> bool:
    """True when *candidate* is *ancestor* or below it in the class hierarchy."""
    if candidate == ancestor:
        return True
    frontier = [candidate]
    seen: set[str] = set()
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        for parent in class_parents(current):
            if parent == ancestor:
                return True
            frontier.append(parent)
    return False
