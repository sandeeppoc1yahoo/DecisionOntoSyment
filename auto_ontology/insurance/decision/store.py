# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persist the rule set so a decision can be explained against a catalog.

The evaluators are code; this table is the certified, queryable statement of
what those rules mean and what a failure of each one does. `insurance_decision_rule`
also lets an operator disable a rule in a future version without deleting it -
though the evaluator list, not the table, is what the engine runs: a disabled
rule must never be a silently skipped one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, delete, select

from auto_ontology.insurance.db import rows
from auto_ontology.insurance.decision.rules import RULES, RULES_VERSION
from auto_ontology.insurance.schema import insurance_decision_rule


def sync_decision_rules(connection: Connection) -> int:
    connection.execute(delete(insurance_decision_rule))
    for rule in RULES:
        connection.execute(
            insurance_decision_rule.insert().values(
                id=rule.id,
                name=rule.name,
                category=rule.category,
                description=rule.description,
                severity=rule.severity,
                on_fail_action=rule.on_fail_action,
                expression=rule.expression,
                version=RULES_VERSION,
                priority=rule.priority,
                is_active=True,
            )
        )
    return len(RULES)


def load_decision_rules(connection: Connection) -> list[dict[str, Any]]:
    return rows(
        connection,
        select(insurance_decision_rule).order_by(insurance_decision_rule.c.priority),
    )
