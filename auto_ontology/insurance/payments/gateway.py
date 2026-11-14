# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Simulated direct-deposit gateway.

**No real transaction is executed anywhere in this module.** Every provider
reference is prefixed ``SIM-``, the account numbers are derived pseudonyms, and
the only side effects are rows in this database.

The controls are real, because they are the part worth learning:

* **Authorisation before movement.** A payment is only created from a current
  ``AUTO_APPROVED`` decision, and only to a beneficiary the policy designates.
  A decision is not a blank cheque: the gateway re-reads it.
* **Idempotency.** The caller supplies a key. The same key with the same amount,
  decision and beneficiary returns the original transaction
  (``idempotent_replay: true``); the same key with different details is a 409.
  Retries after a timeout must not pay twice.
* **Human approval.** Above the configured limit a named approver is required,
  even though the claim itself was auto-approved. The two controls are
  independent on purpose: one says the claim is payable, the other says this
  amount may leave the bank.
* **Verification.** Settlement and verification are separate states. A payment
  is ``SETTLED`` when the simulated rail accepts it and ``VERIFIED`` only after
  the amount, beneficiary and audit chain are re-checked.
* **Audit chain.** Authorisation, settlement and verification each append a
  hash-chained event to the claim's correlation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from auto_ontology.insurance import audit, repository
from auto_ontology.insurance.config import InsuranceSettings, get_settings
from auto_ontology.insurance.db import new_id, now_utc
from auto_ontology.insurance.types import as_money
from auto_ontology.insurance.errors import (
    BeneficiaryNotFoundError,
    DecisionNotFoundError,
    IdempotencyConflictError,
    PaymentApprovalRequiredError,
    PaymentNotAllowedError,
    PaymentNotFoundError,
)
from auto_ontology.insurance.schema import (
    insurance_claim,
    insurance_claim_approval_history,
    insurance_decision_result,
    insurance_payment_transaction,
)

METHOD = "SIMULATED_ACH_DIRECT_DEPOSIT"
SIMULATION_NOTICE = (
    "SIMULATED PAYMENT - no funds moved. Provider references are synthetic."
)


@dataclass(frozen=True)
class PaymentRecord:
    id: str
    claim_id: str
    decision_result_id: str
    beneficiary_id: str
    amount: Decimal
    currency: str
    method: str
    status: str
    idempotency_key: str
    authorization_reference: str
    provider_reference: str | None
    account_last4: str
    routing_last4: str
    requires_human_approval: bool
    approved_by: str | None
    approved_at: datetime | None
    settled_at: datetime | None
    verification_status: str
    failure_reason: str | None
    created_at: datetime
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "payment_id": self.id,
            "claim_id": self.claim_id,
            "decision_id": self.decision_result_id,
            "beneficiary_id": self.beneficiary_id,
            "amount": str(as_money(self.amount)),
            "currency": self.currency,
            "method": self.method,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "idempotent_replay": self.idempotent_replay,
            "authorization_reference": self.authorization_reference,
            "provider_reference": self.provider_reference,
            "account": f"****{self.account_last4}",
            "routing": f"****{self.routing_last4}",
            "requires_human_approval": self.requires_human_approval,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "verification_status": self.verification_status,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at.isoformat(),
            "simulation_notice": SIMULATION_NOTICE,
        }


def _row_to_record(row: dict[str, Any], *, replay: bool = False) -> PaymentRecord:
    return PaymentRecord(
        id=row["id"],
        claim_id=row["claim_id"],
        decision_result_id=row["decision_result_id"],
        beneficiary_id=row["beneficiary_id"],
        amount=row["amount"],
        currency=row["currency"],
        method=row["method"],
        status=row["status"],
        idempotency_key=row["idempotency_key"],
        authorization_reference=row["authorization_reference"],
        provider_reference=row["provider_reference"],
        account_last4=row["account_last4"],
        routing_last4=row["routing_last4"],
        requires_human_approval=row["requires_human_approval"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        settled_at=row["settled_at"],
        verification_status=row["verification_status"],
        failure_reason=row["failure_reason"],
        created_at=row["created_at"],
        idempotent_replay=replay,
    )


def _pseudonymous_bank_details(beneficiary_id: str) -> tuple[str, str]:
    """Deterministic fake account/routing last4, derived from the beneficiary.

    Deterministic so a replayed payment is recognisably the same account, and
    derived so no real bank details are ever needed by the demo.
    """
    digest = hashlib.sha256(beneficiary_id.encode("utf-8")).hexdigest()
    return digest[:4].upper(), digest[4:8].upper()


class SimulatedPaymentGateway:
    """Direct-deposit simulation with the controls a real rail requires."""

    def __init__(self, settings: InsuranceSettings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- authorise and settle ---------------------------------------------
    def initiate(
        self,
        connection: Connection,
        *,
        claim_id: str,
        idempotency_key: str,
        actor: str,
        beneficiary_id: str | None = None,
        approver_id: str | None = None,
    ) -> PaymentRecord:
        if not idempotency_key or len(idempotency_key) < 8:
            raise PaymentNotAllowedError(
                "An idempotency key of at least 8 characters is required"
            )

        decision = self._current_decision(connection, claim_id)
        if decision is None:
            raise DecisionNotFoundError(claim_id)
        if decision["status"] != "AUTO_APPROVED":
            raise PaymentNotAllowedError(
                f"Claim {claim_id!r} has decision status {decision['status']}; "
                "only AUTO_APPROVED claims may be paid"
            )

        claim = repository.get_claim(connection, claim_id)
        assert claim is not None  # guaranteed by the decision's foreign key
        policy = repository.get_policy(connection, claim["policy_id"])
        assert policy is not None
        designations = [
            d
            for d in repository.get_beneficiaries(connection, policy["id"])
            if d["designation_type"] == "PRIMARY"
        ]

        if beneficiary_id is None:
            if len(designations) != 1:
                raise PaymentNotAllowedError(
                    "Payment requires an explicit beneficiary when the policy has "
                    f"{len(designations)} primary designations"
                )
            target = designations[0]
        else:
            matches = [d for d in designations if d["beneficiary_id"] == beneficiary_id]
            if not matches:
                raise BeneficiaryNotFoundError(beneficiary_id)
            target = matches[0]

        if target["verification_status"] != "VERIFIED":
            raise PaymentNotAllowedError(
                f"Beneficiary {target['beneficiary_id']} is not verified"
            )

        amount = as_money(decision["net_payout_amount"])
        if amount <= Decimal("0.00"):
            raise PaymentNotAllowedError("Decision authorises no payable amount")

        existing = (
            connection.execute(
                select(insurance_payment_transaction).where(
                    insurance_payment_transaction.c.idempotency_key == idempotency_key
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            row = dict(existing)
            same_request = (
                row["claim_id"] == claim_id
                and row["decision_result_id"] == decision["id"]
                and as_money(row["amount"]) == amount
                and row["beneficiary_id"] == target["beneficiary_id"]
            )
            if not same_request:
                raise IdempotencyConflictError(
                    f"Idempotency key {idempotency_key!r} was already used for a "
                    "different payment"
                )
            return _row_to_record(row, replay=True)

        already_paid = (
            connection.execute(
                select(insurance_payment_transaction).where(
                    insurance_payment_transaction.c.claim_id == claim_id,
                    insurance_payment_transaction.c.status.in_(
                        ("AUTHORIZED", "SETTLED")
                    ),
                )
            )
            .mappings()
            .first()
        )
        if already_paid is not None:
            raise PaymentNotAllowedError(
                f"Claim {claim_id!r} already has payment {already_paid['id']}"
            )

        requires_human = amount > self.settings.payment_human_approval_limit
        if requires_human and not approver_id:
            raise PaymentApprovalRequiredError(
                f"${amount:,.2f} exceeds the ${self.settings.payment_human_approval_limit:,.2f} "
                "automated payment limit; a named human approver is required"
            )
        approved_by = approver_id if requires_human else "system:payment-gateway"
        timestamp = now_utc()
        payment_id = new_id("PAY")
        authorization_reference = (
            "AUTH-"
            + hashlib.sha256(
                json.dumps(
                    {
                        "claim": claim_id,
                        "decision": decision["id"],
                        "beneficiary": target["beneficiary_id"],
                        "amount": str(amount),
                        "key": idempotency_key,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            )
            .hexdigest()[:16]
            .upper()
        )
        account_last4, routing_last4 = _pseudonymous_bank_details(
            target["beneficiary_id"]
        )

        connection.execute(
            insurance_payment_transaction.insert().values(
                id=payment_id,
                claim_id=claim_id,
                decision_result_id=decision["id"],
                beneficiary_id=target["beneficiary_id"],
                amount=amount,
                currency=decision["currency"],
                method=METHOD,
                status="AUTHORIZED",
                idempotency_key=idempotency_key,
                authorization_reference=authorization_reference,
                provider_reference=None,
                account_last4=account_last4,
                routing_last4=routing_last4,
                requires_human_approval=requires_human,
                approved_by=approved_by,
                approved_at=timestamp if requires_human else None,
                settled_at=None,
                verification_status="PENDING",
                failure_reason=None,
                created_at=timestamp,
            )
        )
        audit.record_event(
            connection,
            actor_type="HUMAN" if requires_human else "SYSTEM",
            actor_id=approved_by,
            action="PAYMENT_AUTHORIZED",
            entity_type="PAYMENT_TRANSACTION",
            entity_id=payment_id,
            correlation_id=claim_id,
            payload={
                "amount": str(amount),
                "currency": decision["currency"],
                "beneficiary_id": target["beneficiary_id"],
                "decision_id": decision["id"],
                "authorization_reference": authorization_reference,
                "requires_human_approval": requires_human,
            },
        )

        provider_reference = (
            "SIM-ACH-"
            + hashlib.sha256(authorization_reference.encode("utf-8"))
            .hexdigest()[:12]
            .upper()
        )
        settled_at = now_utc()
        connection.execute(
            insurance_payment_transaction.update()
            .where(insurance_payment_transaction.c.id == payment_id)
            .values(
                status="SETTLED",
                provider_reference=provider_reference,
                settled_at=settled_at,
                verification_status="PENDING",
            )
        )
        connection.execute(
            insurance_claim.update()
            .where(insurance_claim.c.id == claim_id)
            .values(status="PAID")
        )
        connection.execute(
            insurance_claim_approval_history.insert().values(
                id=new_id("APH"),
                claim_id=claim_id,
                decision_result_id=decision["id"],
                from_status=claim["status"],
                to_status="PAID",
                action="PAYMENT_SETTLED",
                actor_type="SYSTEM",
                actor_id="system:payment-gateway",
                approval_level="AUTOMATED" if not requires_human else "HUMAN_APPROVED",
                reason=(
                    f"Settled {decision['currency']} {amount:,.2f} to beneficiary "
                    f"{target['beneficiary_id']} via {METHOD}"
                ),
                created_at=settled_at,
            )
        )
        audit.record_event(
            connection,
            actor_type="SYSTEM",
            actor_id="system:payment-gateway",
            action="PAYMENT_SETTLED",
            entity_type="PAYMENT_TRANSACTION",
            entity_id=payment_id,
            correlation_id=claim_id,
            payload={
                "provider_reference": provider_reference,
                "amount": str(amount),
                "simulated": True,
            },
        )

        return self.get(connection, payment_id)

    # -- verification ------------------------------------------------------
    def verify(self, connection: Connection, payment_id: str) -> dict[str, Any]:
        """Re-check a settled payment and mark it verified.

        The checks are the ones a treasury reconciliation would run: the amount
        still equals the decision, the beneficiary is still designated, the
        payment settled, and the claim's audit chain is intact.
        """
        payment = self.get(connection, payment_id)
        decision = (
            connection.execute(
                select(insurance_decision_result).where(
                    insurance_decision_result.c.id == payment.decision_result_id
                )
            )
            .mappings()
            .first()
        )
        claim = repository.get_claim(connection, payment.claim_id)
        assert claim is not None
        designations = repository.get_beneficiaries(connection, claim["policy_id"])

        checks = [
            {
                "check": "DECISION_APPROVED",
                "passed": bool(decision) and decision["status"] == "AUTO_APPROVED",
            },
            {
                "check": "AMOUNT_MATCHES_DECISION",
                "passed": bool(decision)
                and as_money(decision["net_payout_amount"]) == as_money(payment.amount),
            },
            {
                "check": "BENEFICIARY_DESIGNATED",
                "passed": any(
                    d["beneficiary_id"] == payment.beneficiary_id
                    and d["designation_type"] == "PRIMARY"
                    and d["verification_status"] == "VERIFIED"
                    for d in designations
                ),
            },
            {"check": "PAYMENT_SETTLED", "passed": payment.status == "SETTLED"},
            {
                "check": "AUDIT_CHAIN_INTACT",
                "passed": audit.verify_chain(connection, payment.claim_id)["intact"],
            },
        ]
        passed = all(check["passed"] for check in checks)
        verification = "VERIFIED" if passed else "FAILED"
        connection.execute(
            insurance_payment_transaction.update()
            .where(insurance_payment_transaction.c.id == payment_id)
            .values(verification_status=verification)
        )
        audit.record_event(
            connection,
            actor_type="SYSTEM",
            actor_id="system:payment-verification",
            action=f"PAYMENT_VERIFICATION_{verification}",
            entity_type="PAYMENT_TRANSACTION",
            entity_id=payment_id,
            correlation_id=payment.claim_id,
            payload={"checks": checks},
        )
        return {
            "payment_id": payment_id,
            "verification_status": verification,
            "checks": checks,
            "audit_chain": audit.verify_chain(connection, payment.claim_id),
        }

    # -- reads -------------------------------------------------------------
    def get(self, connection: Connection, payment_id: str) -> PaymentRecord:
        row = (
            connection.execute(
                select(insurance_payment_transaction).where(
                    insurance_payment_transaction.c.id == payment_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise PaymentNotFoundError(payment_id)
        return _row_to_record(dict(row))

    def list_for_claim(
        self, connection: Connection, claim_id: str
    ) -> list[PaymentRecord]:
        result = connection.execute(
            select(insurance_payment_transaction)
            .where(insurance_payment_transaction.c.claim_id == claim_id)
            .order_by(insurance_payment_transaction.c.created_at)
        ).mappings()
        return [_row_to_record(dict(row)) for row in result]

    @staticmethod
    def _current_decision(
        connection: Connection, claim_id: str
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                select(insurance_decision_result)
                .where(
                    insurance_decision_result.c.claim_id == claim_id,
                    insurance_decision_result.c.is_current.is_(True),
                )
                .order_by(insurance_decision_result.c.decided_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
