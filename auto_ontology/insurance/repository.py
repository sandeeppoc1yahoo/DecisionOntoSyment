# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reads for the claim domain.

One module so the semantic engine, the graph builder, the ontology validator and
the decision engine all see the same rows in the same shape. If each layer wrote
its own join, "which beneficiary is primary" could differ between the number on
the decision and the number on the payment - the classic failure of duplicated
business logic that a semantic layer exists to prevent.

All functions take a SQLAlchemy connection and return plain dicts: the domain
does not use an ORM, matching the platform's Core-only DAL.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, and_, select

from auto_ontology.insurance.db import one, rows
from auto_ontology.insurance.schema import (
    insurance_beneficiary,
    insurance_cause_of_death,
    insurance_claim,
    insurance_claim_evidence,
    insurance_death_certificate,
    insurance_insured_person,
    insurance_legal_restriction,
    insurance_party,
    insurance_policy,
    insurance_policy_beneficiary,
    insurance_policy_exclusion,
    insurance_policy_loan,
    insurance_policyholder,
)

#: Claim states that still compete for the same death benefit.
OPEN_CLAIM_STATUSES = ("SUBMITTED", "UNDER_REVIEW", "APPROVED")


def full_name(person: dict[str, Any] | None) -> str:
    """Display name from a party row, tolerant of missing optional parts."""
    if not person:
        return "Unknown"
    parts = [
        person.get("first_name"),
        person.get("middle_name"),
        person.get("last_name"),
    ]
    return " ".join(part for part in parts if part)


def get_claim(connection: Any, claim_id: str) -> dict[str, Any] | None:
    return one(
        connection, select(insurance_claim).where(insurance_claim.c.id == claim_id)
    )


def get_claim_by_number(connection: Any, claim_number: str) -> dict[str, Any] | None:
    return one(
        connection,
        select(insurance_claim).where(insurance_claim.c.claim_number == claim_number),
    )


def list_claims(connection: Any, *, limit: int = 100) -> list[dict[str, Any]]:
    statement: Select = (
        select(insurance_claim)
        .order_by(insurance_claim.c.submitted_at.desc())
        .limit(limit)
    )
    return rows(connection, statement)


def get_policy(connection: Any, policy_id: str) -> dict[str, Any] | None:
    return one(
        connection, select(insurance_policy).where(insurance_policy.c.id == policy_id)
    )


def get_policy_by_number(connection: Any, number: str) -> dict[str, Any] | None:
    return one(
        connection,
        select(insurance_policy).where(insurance_policy.c.policy_number == number),
    )


def get_party(connection: Any, party_id: str) -> dict[str, Any] | None:
    return one(
        connection, select(insurance_party).where(insurance_party.c.id == party_id)
    )


def get_insured(connection: Any, insured_person_id: str) -> dict[str, Any] | None:
    """Insured role joined to the party it references."""
    statement = (
        select(
            insurance_insured_person.c.id.label("insured_person_id"),
            insurance_insured_person.c.party_id,
            insurance_insured_person.c.occupation,
            insurance_insured_person.c.smoker_status,
            insurance_party.c.first_name,
            insurance_party.c.middle_name,
            insurance_party.c.last_name,
            insurance_party.c.date_of_birth,
            insurance_party.c.email,
            insurance_party.c.phone,
        )
        .select_from(
            insurance_insured_person.join(
                insurance_party,
                insurance_insured_person.c.party_id == insurance_party.c.id,
            )
        )
        .where(insurance_insured_person.c.id == insured_person_id)
    )
    return one(connection, statement)


def get_policyholder(connection: Any, policyholder_id: str) -> dict[str, Any] | None:
    """The owner role joined to its party row.

    The role table exists so ownership can be asserted (and extended to
    organisations) without duplicating the person's details; a reader still
    wants the party row, so the join happens here once.
    """
    statement = (
        select(
            insurance_policyholder.c.id.label("policyholder_id"),
            insurance_policyholder.c.customer_number,
            insurance_party.c.id.label("party_id"),
            insurance_party.c.first_name,
            insurance_party.c.middle_name,
            insurance_party.c.last_name,
            insurance_party.c.date_of_birth,
        )
        .select_from(
            insurance_policyholder.join(
                insurance_party,
                insurance_policyholder.c.party_id == insurance_party.c.id,
            )
        )
        .where(insurance_policyholder.c.id == policyholder_id)
    )
    return one(connection, statement)


def get_beneficiaries(connection: Any, policy_id: str) -> list[dict[str, Any]]:
    """Active designations with the beneficiary's party details.

    ``allocation_percent`` is policy-specific and comes from the designation
    row, not from the beneficiary. The same person can hold 100% on one policy
    and 50% on another.
    """
    statement = (
        select(
            insurance_policy_beneficiary.c.id.label("designation_id"),
            insurance_policy_beneficiary.c.policy_id,
            insurance_policy_beneficiary.c.beneficiary_id,
            insurance_policy_beneficiary.c.allocation_percent,
            insurance_policy_beneficiary.c.designation_order,
            insurance_policy_beneficiary.c.designation_type,
            insurance_policy_beneficiary.c.effective_from,
            insurance_beneficiary.c.party_id,
            insurance_beneficiary.c.relationship_to_insured,
            insurance_beneficiary.c.beneficiary_class,
            insurance_beneficiary.c.verification_status,
            insurance_beneficiary.c.verified_at,
            insurance_party.c.first_name,
            insurance_party.c.middle_name,
            insurance_party.c.last_name,
            insurance_party.c.date_of_birth,
            insurance_party.c.email,
        )
        .select_from(
            insurance_policy_beneficiary.join(
                insurance_beneficiary,
                insurance_policy_beneficiary.c.beneficiary_id
                == insurance_beneficiary.c.id,
            ).join(
                insurance_party,
                insurance_beneficiary.c.party_id == insurance_party.c.id,
            )
        )
        .where(
            and_(
                insurance_policy_beneficiary.c.policy_id == policy_id,
                insurance_policy_beneficiary.c.is_active.is_(True),
            )
        )
        .order_by(insurance_policy_beneficiary.c.designation_order)
    )
    result = rows(connection, statement)
    for row in result:
        row["full_name"] = full_name(row)
    return result


def get_loans(connection: Any, policy_id: str) -> list[dict[str, Any]]:
    return rows(
        connection,
        select(insurance_policy_loan)
        .where(insurance_policy_loan.c.policy_id == policy_id)
        .order_by(insurance_policy_loan.c.taken_date),
    )


def get_exclusions(connection: Any, policy_id: str) -> list[dict[str, Any]]:
    return rows(
        connection,
        select(insurance_policy_exclusion).where(
            insurance_policy_exclusion.c.policy_id == policy_id
        ),
    )


def get_restrictions(
    connection: Any, policy_id: str, *, active_only: bool = True
) -> list[dict[str, Any]]:
    statement = select(insurance_legal_restriction).where(
        insurance_legal_restriction.c.policy_id == policy_id
    )
    if active_only:
        statement = statement.where(insurance_legal_restriction.c.status == "ACTIVE")
    return rows(connection, statement)


def get_evidence(connection: Any, claim_id: str) -> list[dict[str, Any]]:
    return rows(
        connection,
        select(insurance_claim_evidence).where(
            insurance_claim_evidence.c.claim_id == claim_id
        ),
    )


def get_certificate(connection: Any, certificate_id: str) -> dict[str, Any] | None:
    return one(
        connection,
        select(insurance_death_certificate).where(
            insurance_death_certificate.c.id == certificate_id
        ),
    )


def get_cause(connection: Any, code: str) -> dict[str, Any] | None:
    return one(
        connection,
        select(insurance_cause_of_death).where(insurance_cause_of_death.c.code == code),
    )


def get_competing_claims(
    connection: Any, *, claim_id: str, policy_id: str
) -> list[dict[str, Any]]:
    """Other claims still open on the same policy.

    Includes ``APPROVED`` as well as ``SUBMITTED``/``UNDER_REVIEW``: a second
    claim on the same policy is a dispute whether or not one has already been
    decided. ``PAID`` is excluded because payment settles the benefit.
    """
    return rows(
        connection,
        select(insurance_claim).where(
            and_(
                insurance_claim.c.policy_id == policy_id,
                insurance_claim.c.id != claim_id,
                insurance_claim.c.status.in_(OPEN_CLAIM_STATUSES),
            )
        ),
    )
