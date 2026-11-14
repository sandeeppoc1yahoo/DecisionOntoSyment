# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persist the semantic catalog into ``insurance_semantic_definition``.

The registry in ``definitions.py`` is authoritative; this table is its
queryable, certified projection - what a steward reviews, what the API lists,
and what an auditor reads to learn which definition version produced a number.
One row per definition name, upserted, so re-seeding cannot fork a definition.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, delete, select

from auto_ontology.insurance.db import new_id, now_utc, rows
from auto_ontology.insurance.schema import insurance_semantic_definition
from auto_ontology.insurance.semantic.definitions import SEMANTIC_DEFINITIONS


def sync_semantic_definitions(connection: Connection) -> int:
    """Write every definition, replacing the previous catalog."""
    connection.execute(delete(insurance_semantic_definition))
    timestamp = now_utc()
    for definition in SEMANTIC_DEFINITIONS:
        connection.execute(
            insurance_semantic_definition.insert().values(
                id=new_id("SEM"),
                name=definition.name,
                display_name=definition.display_name,
                description=definition.description,
                definition_kind=definition.definition_kind,
                formula=definition.formula,
                unit=definition.unit,
                depends_on=list(definition.depends_on),
                version=definition.version,
                owner=definition.owner,
                is_certified=definition.is_certified,
                certified_by="claims-governance" if definition.is_certified else None,
                effective_from=timestamp,
                updated_at=timestamp,
            )
        )
    return len(SEMANTIC_DEFINITIONS)


def load_semantic_definitions(connection: Connection) -> list[dict[str, Any]]:
    return rows(
        connection,
        select(insurance_semantic_definition).order_by(
            insurance_semantic_definition.c.name
        ),
    )
