# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the simulated payment rail and its controls."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from auto_ontology.insurance.db import connection_scope, transaction
from auto_ontology.insurance.decision.engine import decide_claim
from auto_ontology.insurance.errors import (
    PaymentApprovalRequiredError,
    PaymentNotAllowedError,
)
from auto_ontology.insurance.payments.gateway import SimulatedPaymentGateway
from auto_ontology.insurance.schema import (
    insurance_claim,
    insurance_payment_transaction,
)
from auto_ontology.insurance.seed import SCENARIO_A_CLAIM_ID, SCENARIO_B_CLAIM_ID

KEY = "claim-CLM-2001-payout-1"


def _decide(claim_id: str = SCENARIO_A_CLAIM_ID) -> None:
    with transaction() as connection:
        decide_claim(connection, claim_id)


def test_payment_requires_authorised_approval_above_limit(fresh_db: None) -> None:
    _decide()
    with transaction() as connection, pytest.raises(PaymentApprovalRequiredError):
        SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=KEY,
            actor="officer:claims-intake",
        )


def test_simulated_direct_deposit_settles(fresh_db: None) -> None:
    _decide()
    with transaction() as connection:
        payment = SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=KEY,
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )

    assert payment.status == "SETTLED"
    assert payment.amount == Decimal("250000.00")
    assert payment.currency == "USD"
    assert payment.method == "SIMULATED_ACH_DIRECT_DEPOSIT"
    assert payment.provider_reference.startswith("SIM-ACH-")
    assert payment.authorization_reference.startswith("AUTH-")
    assert payment.requires_human_approval is True
    assert payment.approved_by == "officer:j.mensah"
    assert payment.verification_status == "PENDING"
    assert payment.account_last4 and payment.routing_last4


def test_claim_is_paid_and_no_second_payment_is_allowed(fresh_db: None) -> None:
    _decide()
    with transaction() as connection:
        first = SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=KEY,
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )

    with connection_scope() as connection:
        status = connection.execute(
            select(insurance_claim.c.status).where(
                insurance_claim.c.id == SCENARIO_A_CLAIM_ID
            )
        ).scalar_one()
        count = connection.execute(
            select(func.count()).select_from(insurance_payment_transaction)
        ).scalar_one()

    assert status == "PAID"
    assert count == 1
    assert first.id

    with transaction() as connection, pytest.raises(PaymentNotAllowedError):
        SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key="claim-CLM-2001-payout-2",
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )


def test_idempotent_replay_returns_the_original(fresh_db: None) -> None:
    _decide()
    with transaction() as connection:
        first = SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=KEY,
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )
    with transaction() as connection:
        replay = SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=KEY,
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )

    assert replay.id == first.id
    assert replay.idempotent_replay is True
    with connection_scope() as connection:
        count = connection.execute(
            select(func.count()).select_from(insurance_payment_transaction)
        ).scalar_one()
    assert count == 1


def test_verification_reconciles_the_payment(fresh_db: None) -> None:
    _decide()
    with transaction() as connection:
        payment = SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=KEY,
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )
    with transaction() as connection:
        result = SimulatedPaymentGateway().verify(connection, payment.id)

    assert result["verification_status"] == "VERIFIED"
    assert all(check["passed"] for check in result["checks"])
    assert result["audit_chain"]["intact"] is True


def test_manual_review_claim_cannot_be_paid(fresh_db: None) -> None:
    _decide(SCENARIO_B_CLAIM_ID)
    with transaction() as connection, pytest.raises(PaymentNotAllowedError):
        SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_B_CLAIM_ID,
            idempotency_key="claim-CLM-2002-payout-1",
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )


def test_payment_requires_a_decision(fresh_db: None) -> None:
    from auto_ontology.insurance.errors import DecisionNotFoundError

    with transaction() as connection, pytest.raises(DecisionNotFoundError):
        SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key="claim-CLM-2001-payout-3",
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )


def test_short_idempotency_key_is_rejected(fresh_db: None) -> None:
    _decide()
    with transaction() as connection, pytest.raises(PaymentNotAllowedError):
        SimulatedPaymentGateway().initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key="short",
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )
