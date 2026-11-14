# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One call that makes the domain runnable.

Creates the tables, materialises the ontology, certifies the semantic catalog
and the rule set, and seeds the two sample scenarios. Used by the demo, the API
lifespan and the integration tests, so all three start from exactly the same
state.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from auto_ontology.insurance.db import create_schema, transaction
from auto_ontology.insurance.decision.store import sync_decision_rules
from auto_ontology.insurance.ontology.store import materialize_ontology
from auto_ontology.insurance.schema import insurance_party
from auto_ontology.insurance.seed import seed_demo_data, seed_reference_causes
from auto_ontology.insurance.semantic.catalog import sync_semantic_definitions


def bootstrap(*, seed: bool = True) -> dict[str, Any]:
    """Prepare the database and return a short report of what was written."""
    create_schema()
    report: dict[str, Any] = {}
    with transaction() as connection:
        report["ontology"] = materialize_ontology(connection)
        report["semantic_definitions"] = sync_semantic_definitions(connection)
        report["decision_rules"] = sync_decision_rules(connection)
        seed_reference_causes(connection)
        if seed:
            report["sample_data"] = seed_demo_data(connection)
        else:
            count = connection.execute(
                select(func.count()).select_from(insurance_party)
            ).scalar_one()
            report["sample_data"] = {"seeded": False, "parties": int(count)}
    return report


def reset_and_seed() -> dict[str, Any]:
    """Drop and rebuild everything. Intended for demos and tests only."""
    from auto_ontology.insurance.db import reset_schema

    reset_schema()
    return bootstrap(seed=True)


if __name__ == "__main__":
    import json

    print(json.dumps(bootstrap(seed=True), indent=2, default=str))
