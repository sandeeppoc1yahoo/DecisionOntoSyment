# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for adjudication, including the two contractual denial paths."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update

from auto_ontology.insurance.db import connection_scope, transaction
from auto_ontology.insurance.decision.engine import (
    AUTO_APPROVED,
    DENIED,
    MANUAL_REVIEW,
    decide_claim,
    get_current_decision,
)
from auto_ontology.insurance.schema import (
    insurance_claim,
    insurance_claim_approval_history,
    insurance_death_certificate,
    insurance_decision_result,
)
from auto_ontology.insurance.seed import SCENARIO_A_CLAIM_ID, SCENARIO_B_CLAIM_ID


def test_standard_death_claim_is_auto_approved(fresh_db: None) -> None:
    with transaction() as connection:
        decision = decide_claim(connection, SCENARIO_A_CLAIM_ID)

    assert decision.status == AUTO_APPROVED
    assert decision.net_payout_amount == Decimal("250000.00")
    assert decision.currency == "USD"
    assert decision.confidence_score == 1.0
    assert len(decision.rules_applied) == 15
    assert all(rule["status"] == "PASS" for rule in decision.rules_applied)
    assert decision.audit_reference.startswith("AUD-")
    assert decision.decision_path.startswith("AUTO_APPROVED")


def test_decision_carries_every_layer(fresh_db: None) -> None:
    with transaction() as connection:
        decision = decide_claim(connection, SCENARIO_A_CLAIM_ID)

    semantic_names = {
        value["name"] for value in decision.semantic_calculations["values"]
    }
    assert "net_death_benefit" in semantic_names
    assert len(decision.ontology_validation) == 9
    assert decision.kg_evidence["evidence_chain_text"].startswith(
        "InsuredPerson Robert James Hayes"
    )
    assert decision.kg_evidence["ontology_violations"] == []
    assert "AUTO_APPROVED" in decision.explanation
    assert "1,825 days" in decision.explanation


def test_claim_status_and_history_are_updated(fresh_db: None) -> None:
    with transaction() as connection:
        decide_claim(connection, SCENARIO_A_CLAIM_ID)

    with connection_scope() as connection:
        status = connection.execute(
            select(insurance_claim.c.status).where(
                insurance_claim.c.id == SCENARIO_A_CLAIM_ID
            )
        ).scalar_one()
        history = (
            connection.execute(
                select(insurance_claim_approval_history).where(
                    insurance_claim_approval_history.c.claim_id == SCENARIO_A_CLAIM_ID
                )
            )
            .mappings()
            .all()
        )

    assert status == "APPROVED"
    assert len(history) == 1
    assert history[0]["action"] == "DECISION_AUTO_APPROVED"
    assert history[0]["approval_level"] == "AUTOMATED"


def test_redecision_supersedes_the_previous_result(fresh_db: None) -> None:
    with transaction() as connection:
        first = decide_claim(connection, SCENARIO_A_CLAIM_ID)
    with transaction() as connection:
        second = decide_claim(connection, SCENARIO_A_CLAIM_ID)

    assert first.id != second.id
    with connection_scope() as connection:
        current = get_current_decision(connection, SCENARIO_A_CLAIM_ID)
        rows = connection.execute(select(insurance_decision_result)).mappings().all()
    assert current is not None and current.id == second.id
    assert sum(1 for row in rows if row["is_current"]) == 1


def test_legal_restriction_refers_the_claim(fresh_db: None) -> None:
    with transaction() as connection:
        decision = decide_claim(connection, SCENARIO_B_CLAIM_ID)
    assert decision.status == MANUAL_REVIEW
    assert decision.net_payout_amount == Decimal("0.00")
    failed = [
        rule["rule_id"] for rule in decision.rules_applied if rule["status"] == "FAIL"
    ]
    assert failed == ["R10_NO_LEGAL_RESTRICTIONS"]


def test_applicable_exclusion_denies(fresh_db: None) -> None:
    """A natural-death policy still denies an aviation death it excludes."""
    with transaction() as connection:
        connection.execute(
            update(insurance_claim)
            .where(insurance_claim.c.id == SCENARIO_A_CLAIM_ID)
            .values(cause_code="V95.40")
        )
        connection.execute(
            update(insurance_death_certificate)
            .where(insurance_death_certificate.c.id == "DC-9001")
            .values(cause_code="V95.40")
        )
    with transaction() as connection:
        decision = decide_claim(connection, SCENARIO_A_CLAIM_ID)

    assert decision.status == DENIED
    assert decision.net_payout_amount == Decimal("0.00")
    failed = [
        rule["rule_id"] for rule in decision.rules_applied if rule["status"] == "FAIL"
    ]
    assert failed == ["R04_NO_APPLICABLE_EXCLUSION"]
    assert "EX-AVIATION" in decision.decision_path


def test_contestability_only_exclusion_applies_inside_the_window(
    fresh_db: None,
) -> None:
    """Suicide inside two years is excluded; the same death later is not."""
    with transaction() as connection:
        connection.execute(
            update(insurance_claim)
            .where(insurance_claim.c.id == SCENARIO_A_CLAIM_ID)
            .values(cause_code="X71", date_of_death=datetime(2022, 1, 1))
        )
        connection.execute(
            update(insurance_death_certificate)
            .where(insurance_death_certificate.c.id == "DC-9001")
            .values(cause_code="X71", date_of_death=datetime(2022, 1, 1))
        )
    with transaction() as connection:
        decision = decide_claim(connection, SCENARIO_A_CLAIM_ID)

    assert decision.status == DENIED
    assert decision.semantic_calculations["values"]
    failed = [
        rule["rule_id"] for rule in decision.rules_applied if rule["status"] == "FAIL"
    ]
    assert "R04_NO_APPLICABLE_EXCLUSION" in failed
    assert "EX-SUICIDE-2Y" in decision.decision_path


def test_decision_is_attributed_to_the_system(fresh_db: None) -> None:
    with transaction() as connection:
        decision = decide_claim(connection, SCENARIO_A_CLAIM_ID)
    assert decision.decided_by == "system:decision-engine"
