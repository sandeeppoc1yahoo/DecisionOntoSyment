# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the semantic layer.

The scenario's expected numbers are asserted exactly: 1,825 active days, a
730-day contestability period, a $250,000 net benefit and payout, zero
outstanding loan balance. If a definition changes, these are the tests that
should fail first.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_ontology.insurance.db import connection_scope
from auto_ontology.insurance.seed import SCENARIO_A_CLAIM_ID, SCENARIO_B_CLAIM_ID
from auto_ontology.insurance.semantic.definitions import (
    DEFINITIONS_BY_NAME,
    SEMANTIC_DEFINITIONS,
    validate_registry,
)
from auto_ontology.insurance.semantic.engine import SemanticEngine


def test_registry_is_acyclic_and_complete() -> None:
    validate_registry()
    for definition in SEMANTIC_DEFINITIONS:
        assert definition.formula, definition.name
        assert definition.version, definition.name
        assert definition.is_certified, definition.name


def test_every_definition_is_evaluated(fresh_db: None) -> None:
    with connection_scope() as connection:
        result = SemanticEngine().evaluate(connection, SCENARIO_A_CLAIM_ID)
    assert set(result.values) == set(DEFINITIONS_BY_NAME)
    for value in result.values.values():
        assert value.formula
        assert value.display_value != ""


def test_standard_claim_calculations(fresh_db: None) -> None:
    with connection_scope() as connection:
        result = SemanticEngine().evaluate(connection, SCENARIO_A_CLAIM_ID)

    assert result.get("policy_active_days") == 1825
    assert result.get("contestability_period_days") == 730
    assert result.get("past_contestability") is True
    assert result.get("policy_face_amount") == Decimal("250000.00")
    assert result.get("outstanding_policy_loan_balance") == Decimal("0.00")
    assert result.get("net_death_benefit") == Decimal("250000.00")
    assert result.get("primary_beneficiary_count") == 1
    assert result.get("total_primary_allocation_percent") == Decimal("100.00")
    assert result.get("payout_amount") == Decimal("250000.00")
    assert result.get("claim_eligibility") == "ELIGIBLE"


def test_repaid_loan_does_not_reduce_benefit(fresh_db: None) -> None:
    """A repaid loan is not netted twice: the balance is what counts."""
    with connection_scope() as connection:
        result = SemanticEngine().evaluate(connection, SCENARIO_A_CLAIM_ID)
    assert result.facts["loans"], "scenario should carry a repaid loan"
    assert result.get("outstanding_policy_loan_balance") == Decimal("0.00")


def test_outstanding_loan_reduces_benefit(fresh_db: None) -> None:
    with connection_scope() as connection:
        result = SemanticEngine().evaluate(connection, SCENARIO_B_CLAIM_ID)
    assert result.get("policy_face_amount") == Decimal("400000.00")
    assert result.get("outstanding_policy_loan_balance") == Decimal("25000.00")
    assert result.get("net_death_benefit") == Decimal("375000.00")
    assert result.get("payout_amount") == Decimal("375000.00")


def test_contestability_boundary_is_strict() -> None:
    """The period ends on day 731, not day 730: `>` rather than `>=`."""
    definition = DEFINITIONS_BY_NAME["past_contestability"]
    assert definition.formula == "policy_active_days > contestability_period_days"


def test_apportionment_assigns_rounding_residual() -> None:
    shares = SemanticEngine._apportion(
        Decimal("100.00"),
        [
            {
                "beneficiary_id": "B1",
                "party_id": "P1",
                "allocation_percent": Decimal("33.33"),
            },
            {
                "beneficiary_id": "B2",
                "party_id": "P2",
                "allocation_percent": Decimal("33.33"),
            },
            {
                "beneficiary_id": "B3",
                "party_id": "P3",
                "allocation_percent": Decimal("33.34"),
            },
        ],
    )
    assert [share["_amount"] for share in shares] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert sum(share["_amount"] for share in shares) == Decimal("100.00")


def test_definition_carries_version_and_formula(fresh_db: None) -> None:
    with connection_scope() as connection:
        result = SemanticEngine().evaluate(connection, SCENARIO_A_CLAIM_ID)
    payout = result["payout_amount"]
    assert payout.version == "1.0.0"
    assert "net_death_benefit" in payout.formula
    assert payout.definition_kind == "DERIVED"


def test_unknown_claim_raises(fresh_db: None) -> None:
    from auto_ontology.insurance.errors import ClaimNotFoundError

    with connection_scope() as connection, pytest.raises(ClaimNotFoundError):
        SemanticEngine().evaluate(connection, "CLM-NOPE")
