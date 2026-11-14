# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persist the T-Box into ``insurance_ontology_*``.

Why store an ontology that already exists in Python? Because the *catalog* is a
shared artifact: the API serves it, the knowledge-graph builder types nodes with
it, and a reviewer reads it without a deployment. Python remains the source of
truth; this is a materialised, queryable projection, rebuilt on each
``materialize_ontology`` call.

Idempotent by IRI: re-running rewrites descriptions and constraints in place, so
a vocabulary change does not produce duplicate classes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, delete, select

from auto_ontology.insurance.db import new_id, rows
from auto_ontology.insurance.ontology.vocabulary import (
    CLASSES,
    LIO,
    ONTOLOGY_VERSION,
    PROPERTIES,
)
from auto_ontology.insurance.schema import (
    insurance_ontology_entity,
    insurance_ontology_relationship,
)


def materialize_ontology(connection: Connection) -> dict[str, int]:
    """Write every class and property, replacing the previous projection."""
    connection.execute(delete(insurance_ontology_entity))
    connection.execute(delete(insurance_ontology_relationship))

    for concept in CLASSES:
        for index, parent in enumerate(concept.parents or (None,)):
            connection.execute(
                insurance_ontology_entity.insert().values(
                    id=new_id("ONT"),
                    iri=concept.iri if parent is None else f"{concept.iri}~{index}",
                    name=concept.local,
                    entity_kind="CLASS",
                    parent_iri=LIO + parent if parent else None,
                    description=concept.description,
                    constraints=list(concept.constraints),
                    version=ONTOLOGY_VERSION,
                    is_certified=True,
                )
            )

    for prop in PROPERTIES:
        connection.execute(
            insurance_ontology_relationship.insert().values(
                id=new_id("ONR"),
                iri=prop.iri,
                name=prop.local,
                domain_iri=LIO + prop.domain,
                range_iri=LIO + prop.range,
                cardinality=prop.cardinality,
                description=prop.description,
                is_transitive=prop.is_transitive,
                is_symmetric=prop.is_symmetric,
                version=ONTOLOGY_VERSION,
                is_certified=True,
            )
        )

    return {"entities": len(CLASSES), "relationships": len(PROPERTIES)}


def load_ontology(connection: Connection) -> dict[str, Any]:
    """Read the materialised ontology back out.

    Classes are grouped by IRI because a class with several parents has one row
    per parent in storage (a relational way to express multiple inheritance),
    and callers want the class once with its parent list.
    """
    entity_rows = rows(
        connection,
        select(insurance_ontology_entity).order_by(insurance_ontology_entity.c.name),
    )
    relation_rows = rows(
        connection,
        select(insurance_ontology_relationship).order_by(
            insurance_ontology_relationship.c.name
        ),
    )

    grouped: dict[str, dict[str, Any]] = {}
    for row in entity_rows:
        base_iri = row["iri"].split("~", 1)[0]
        entry = grouped.setdefault(
            base_iri,
            {
                "iri": base_iri,
                "name": row["name"],
                "description": row["description"],
                "constraints": row["constraints"],
                "parents": [],
            },
        )
        if row["parent_iri"]:
            entry["parents"].append(row["parent_iri"])

    return {
        "version": ONTOLOGY_VERSION,
        "classes": list(grouped.values()),
        "properties": relation_rows,
        "counts": {"classes": len(grouped), "properties": len(relation_rows)},
    }
