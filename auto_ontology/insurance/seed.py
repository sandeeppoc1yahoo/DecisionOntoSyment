# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic sample data for the running use case.

Two lives, deliberately contrasting:

**Scenario A - the fast-track death claim.** Robert Hayes bought a $250,000
20-year term policy on 2021-06-15. He died of an acute myocardial infarction on
2026-06-14 - 1,825 days later, past the 730-day contestability period. His
spouse Jane is the sole primary beneficiary at 100%, identity-verified. There is
no outstanding loan (an earlier loan was repaid), no legal restriction, no
competing claim, and all four evidence types are verified. This is the case that
must approve end to end.

**Scenario B - the exception.** Daniel Okafor died in a motor-vehicle accident
with an outstanding $25,000 loan and an active court order redirecting the
benefit. Every rule about cover passes; the restriction means a human must
handle it. It exists so the demo and the tests can show that the engine does not
approve merely because it *can*.

Dates are fixed, not relative to today: a reproducible demo whose expected
output changes daily is not a demonstration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, select

from auto_ontology.insurance.ontology.vocabulary import CAUSES, EXCLUSIONS
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

#: Fixed timestamps keep the scenario reproducible.
EFFECTIVE_A = datetime(2021, 6, 15, 0, 0)
DEATH_A = datetime(2026, 6, 14, 0, 0)
SUBMITTED_A = datetime(2026, 6, 17, 9, 30)
VERIFIED_A = datetime(2026, 6, 17, 14, 5)

EFFECTIVE_B = datetime(2020, 1, 10, 0, 0)
DEATH_B = datetime(2026, 5, 30, 0, 0)
SUBMITTED_B = datetime(2026, 6, 1, 8, 15)
VERIFIED_B = datetime(2026, 6, 1, 16, 40)

SCENARIO_A_CLAIM_ID = "CLM-2001"
SCENARIO_B_CLAIM_ID = "CLM-2002"


def _insert(connection: Connection, table: Any, values: dict[str, Any]) -> None:
    connection.execute(table.insert().values(**values))


def seed_reference_causes(connection: Connection) -> None:
    """Upsert the ICD-10 -> ontology-class reference table."""
    for concept in CAUSES.values():
        existing = connection.execute(
            select(insurance_cause_of_death.c.code).where(
                insurance_cause_of_death.c.code == concept.code
            )
        ).first()
        values = {
            "description": concept.description,
            "category": concept.category,
            "ontology_class_iri": concept.class_iri,
            "is_self_inflicted": concept.is_self_inflicted,
            "icd10_chapter": concept.icd10_chapter,
        }
        if existing:
            connection.execute(
                insurance_cause_of_death.update()
                .where(insurance_cause_of_death.c.code == concept.code)
                .values(**values)
            )
        else:
            _insert(
                connection,
                insurance_cause_of_death,
                {"code": concept.code, **values},
            )


def seed_demo_data(connection: Connection) -> dict[str, Any]:
    """Insert the two scenarios if the domain is empty.

    Idempotent: a second call reports ``seeded: False`` rather than duplicating
    parties or policies. Reset explicitly with ``reset_and_seed``.
    """
    seed_reference_causes(connection)

    existing = connection.execute(
        select(func.count()).select_from(insurance_party)
    ).scalar_one()
    if existing:
        return {"seeded": False, "reason": "sample data already present"}

    # -- parties ----------------------------------------------------------
    for party in (
        {
            "id": "PRT-1001",
            "party_type": "PERSON",
            "first_name": "Robert",
            "middle_name": "James",
            "last_name": "Hayes",
            "date_of_birth": datetime(1974, 2, 11),
            "gender": "M",
            "email": "robert.hayes@example.com",
            "phone": "+1-512-555-0142",
            "tax_id_last4": "4821",
        },
        {
            "id": "PRT-1002",
            "party_type": "PERSON",
            "first_name": "Jane",
            "middle_name": "Marie",
            "last_name": "Hayes",
            "date_of_birth": datetime(1976, 8, 3),
            "gender": "F",
            "email": "jane.hayes@example.com",
            "phone": "+1-512-555-0143",
            "tax_id_last4": "7719",
        },
        {
            "id": "PRT-1003",
            "party_type": "PERSON",
            "first_name": "Emily",
            "middle_name": "Rose",
            "last_name": "Hayes",
            "date_of_birth": datetime(2008, 5, 19),
            "gender": "F",
            "email": "emily.hayes@example.com",
        },
        {
            "id": "PRT-1004",
            "party_type": "PERSON",
            "first_name": "Daniel",
            "middle_name": "Chukwu",
            "last_name": "Okafor",
            "date_of_birth": datetime(1982, 11, 27),
            "gender": "M",
            "email": "daniel.okafor@example.com",
            "tax_id_last4": "3350",
        },
        {
            "id": "PRT-1005",
            "party_type": "PERSON",
            "first_name": "Amara",
            "middle_name": "Ngozi",
            "last_name": "Okafor",
            "date_of_birth": datetime(1985, 4, 9),
            "gender": "F",
            "email": "amara.okafor@example.com",
            "tax_id_last4": "9024",
        },
    ):
        _insert(
            connection, insurance_party, {**party, "created_at": datetime(2020, 1, 1)}
        )

    _insert(
        connection,
        insurance_policyholder,
        {
            "id": "PH-1001",
            "party_id": "PRT-1001",
            "customer_number": "CUST-88021",
            "created_at": EFFECTIVE_A,
        },
    )
    _insert(
        connection,
        insurance_policyholder,
        {
            "id": "PH-1002",
            "party_id": "PRT-1004",
            "customer_number": "CUST-88022",
            "created_at": EFFECTIVE_B,
        },
    )
    _insert(
        connection,
        insurance_insured_person,
        {
            "id": "INS-4001",
            "party_id": "PRT-1001",
            "occupation": "Mechanical engineer",
            "smoker_status": "NEVER",
            "height_cm": 180,
            "weight_kg": 84,
            "created_at": EFFECTIVE_A,
        },
    )
    _insert(
        connection,
        insurance_insured_person,
        {
            "id": "INS-4002",
            "party_id": "PRT-1004",
            "occupation": "Logistics manager",
            "smoker_status": "NEVER",
            "height_cm": 176,
            "weight_kg": 79,
            "created_at": EFFECTIVE_B,
        },
    )

    # -- policies ---------------------------------------------------------
    _insert(
        connection,
        insurance_policy,
        {
            "id": "POL-1001",
            "policy_number": "TL-2021-004821",
            "policyholder_id": "PH-1001",
            "insured_person_id": "INS-4001",
            "product_code": "TERM20",
            "product_name": "20-Year Level Term Life",
            "effective_date": EFFECTIVE_A,
            "expiry_date": datetime(2041, 6, 15),
            "face_amount": Decimal("250000.00"),
            "premium_amount": Decimal("118.40"),
            "premium_mode": "MONTHLY",
            "status": "IN_FORCE",
            "issue_state": "TX",
            "contestability_days": 730,
            "created_at": EFFECTIVE_A,
        },
    )
    _insert(
        connection,
        insurance_policy,
        {
            "id": "POL-1002",
            "policy_number": "TL-2020-003377",
            "policyholder_id": "PH-1002",
            "insured_person_id": "INS-4002",
            "product_code": "TERM20",
            "product_name": "20-Year Level Term Life",
            "effective_date": EFFECTIVE_B,
            "expiry_date": datetime(2040, 1, 10),
            "face_amount": Decimal("400000.00"),
            "premium_amount": Decimal("162.75"),
            "premium_mode": "MONTHLY",
            "status": "IN_FORCE",
            "issue_state": "TX",
            "contestability_days": 730,
            "created_at": EFFECTIVE_B,
        },
    )

    # -- beneficiaries and designations -----------------------------------
    _insert(
        connection,
        insurance_beneficiary,
        {
            "id": "BEN-3001",
            "party_id": "PRT-1002",
            "relationship_to_insured": "SPOUSE",
            "beneficiary_class": "PRIMARY",
            "verification_status": "VERIFIED",
            "verified_at": VERIFIED_A,
            "verified_by": "officer:k.nguyen",
            "created_at": EFFECTIVE_A,
        },
    )
    _insert(
        connection,
        insurance_beneficiary,
        {
            "id": "BEN-3002",
            "party_id": "PRT-1003",
            "relationship_to_insured": "CHILD",
            "beneficiary_class": "CONTINGENT",
            "verification_status": "VERIFIED",
            "verified_at": VERIFIED_A,
            "verified_by": "officer:k.nguyen",
            "created_at": EFFECTIVE_A,
        },
    )
    _insert(
        connection,
        insurance_beneficiary,
        {
            "id": "BEN-3003",
            "party_id": "PRT-1005",
            "relationship_to_insured": "SPOUSE",
            "beneficiary_class": "PRIMARY",
            "verification_status": "VERIFIED",
            "verified_at": VERIFIED_B,
            "verified_by": "officer:k.nguyen",
            "created_at": EFFECTIVE_B,
        },
    )
    for designation in (
        {
            "id": "DSG-5001",
            "policy_id": "POL-1001",
            "beneficiary_id": "BEN-3001",
            "allocation_percent": Decimal("100.00"),
            "designation_order": 1,
            "designation_type": "PRIMARY",
            "effective_from": EFFECTIVE_A,
            "is_active": True,
        },
        {
            "id": "DSG-5002",
            "policy_id": "POL-1001",
            "beneficiary_id": "BEN-3002",
            "allocation_percent": Decimal("100.00"),
            "designation_order": 1,
            "designation_type": "CONTINGENT",
            "effective_from": EFFECTIVE_A,
            "is_active": True,
        },
        {
            "id": "DSG-5003",
            "policy_id": "POL-1002",
            "beneficiary_id": "BEN-3003",
            "allocation_percent": Decimal("100.00"),
            "designation_order": 1,
            "designation_type": "PRIMARY",
            "effective_from": EFFECTIVE_B,
            "is_active": True,
        },
    ):
        _insert(
            connection,
            insurance_policy_beneficiary,
            {**designation, "created_at": designation["effective_from"]},
        )

    # -- financial obligations --------------------------------------------
    _insert(
        connection,
        insurance_policy_loan,
        {
            "id": "LN-7001",
            "policy_id": "POL-1001",
            "loan_number": "PL-2023-0912",
            "principal_amount": Decimal("10000.00"),
            "outstanding_balance": Decimal("0.00"),
            "interest_rate_percent": Decimal("5.50"),
            "taken_date": datetime(2023, 2, 1),
            "status": "REPAID",
            "created_at": datetime(2023, 2, 1),
        },
    )
    _insert(
        connection,
        insurance_policy_loan,
        {
            "id": "LN-7002",
            "policy_id": "POL-1002",
            "loan_number": "PL-2024-1188",
            "principal_amount": Decimal("25000.00"),
            "outstanding_balance": Decimal("25000.00"),
            "interest_rate_percent": Decimal("6.00"),
            "taken_date": datetime(2024, 3, 15),
            "status": "OUTSTANDING",
            "created_at": datetime(2024, 3, 15),
        },
    )

    # -- exclusions --------------------------------------------------------
    for exclusion_id, policy_id, code in (
        ("EXC-8001", "POL-1001", "EX-SUICIDE-2Y"),
        ("EXC-8002", "POL-1001", "EX-WAR"),
        ("EXC-8003", "POL-1001", "EX-AVIATION"),
        ("EXC-8004", "POL-1002", "EX-SUICIDE-2Y"),
        ("EXC-8005", "POL-1002", "EX-AVIATION"),
    ):
        concept = EXCLUSIONS[code]
        _insert(
            connection,
            insurance_policy_exclusion,
            {
                "id": exclusion_id,
                "policy_id": policy_id,
                "exclusion_code": concept.code,
                "name": concept.name,
                "description": concept.description,
                "excludes_cause_class": concept.excludes_class,
                "contestability_only": concept.contestability_only,
                "is_active": True,
                "created_at": EFFECTIVE_A if policy_id == "POL-1001" else EFFECTIVE_B,
            },
        )

    # -- death certificates ------------------------------------------------
    _insert(
        connection,
        insurance_death_certificate,
        {
            "id": "DC-9001",
            "certificate_number": "DC-2026-551234",
            "insured_person_id": "INS-4001",
            "date_of_death": DEATH_A,
            "place_of_death": "Mercy General Hospital, Austin, TX",
            "cause_code": "I21.9",
            "manner_of_death": "NATURAL",
            "certifying_physician": "Dr. Alan Whitfield, MD",
            "issued_date": datetime(2026, 6, 16),
            "document_uri": "s3://claims-documents/DC-2026-551234.pdf",
            "document_hash": "sha256:6f1c5d2be0a849a1c2f4f5d3e9b7a8c6d1e2f3a4b5c6d7e8f90123456789abcd",
            "verification_status": "VERIFIED",
            "created_at": VERIFIED_A,
        },
    )
    _insert(
        connection,
        insurance_death_certificate,
        {
            "id": "DC-9002",
            "certificate_number": "DC-2026-560771",
            "insured_person_id": "INS-4002",
            "date_of_death": DEATH_B,
            "place_of_death": "I-35 North, Austin, TX",
            "cause_code": "V89.2",
            "manner_of_death": "ACCIDENT",
            "certifying_physician": "Dr. Priya Raman, MD",
            "issued_date": datetime(2026, 5, 31),
            "document_uri": "s3://claims-documents/DC-2026-560771.pdf",
            "document_hash": "sha256:1234abcd5678ef901234abcd5678ef901234abcd5678ef901234abcd5678ef90",
            "verification_status": "VERIFIED",
            "created_at": VERIFIED_B,
        },
    )

    # -- claims ------------------------------------------------------------
    _insert(
        connection,
        insurance_claim,
        {
            "id": SCENARIO_A_CLAIM_ID,
            "claim_number": "CLM-2026-0001",
            "policy_id": "POL-1001",
            "insured_person_id": "INS-4001",
            "claimant_party_id": "PRT-1002",
            "death_certificate_id": "DC-9001",
            "claim_type": "DEATH",
            "date_of_death": DEATH_A,
            "cause_code": "I21.9",
            "status": "SUBMITTED",
            "channel": "PORTAL",
            "submitted_at": SUBMITTED_A,
            "notes": (
                "Spouse submitted the claim with the certified death certificate "
                "and beneficiary identity documents."
            ),
            "created_at": SUBMITTED_A,
        },
    )
    _insert(
        connection,
        insurance_claim,
        {
            "id": SCENARIO_B_CLAIM_ID,
            "claim_number": "CLM-2026-0002",
            "policy_id": "POL-1002",
            "insured_person_id": "INS-4002",
            "claimant_party_id": "PRT-1005",
            "death_certificate_id": "DC-9002",
            "claim_type": "DEATH",
            "date_of_death": DEATH_B,
            "cause_code": "V89.2",
            "status": "SUBMITTED",
            "channel": "PORTAL",
            "submitted_at": SUBMITTED_B,
            "created_at": SUBMITTED_B,
        },
    )

    evidence_rows = [
        (
            "EVD-6001",
            SCENARIO_A_CLAIM_ID,
            "DEATH_CERTIFICATE",
            "s3://claims-documents/DC-2026-551234.pdf",
            VERIFIED_A,
        ),
        (
            "EVD-6002",
            SCENARIO_A_CLAIM_ID,
            "CLAIM_FORM",
            "s3://claims-documents/CLM-2026-0001-form.pdf",
            VERIFIED_A,
        ),
        (
            "EVD-6003",
            SCENARIO_A_CLAIM_ID,
            "IDENTITY_PROOF",
            "s3://claims-documents/jane-hayes-id.pdf",
            VERIFIED_A,
        ),
        (
            "EVD-6004",
            SCENARIO_A_CLAIM_ID,
            "BENEFICIARY_PROOF",
            "s3://claims-documents/hayes-marriage-certificate.pdf",
            VERIFIED_A,
        ),
        (
            "EVD-6005",
            SCENARIO_B_CLAIM_ID,
            "DEATH_CERTIFICATE",
            "s3://claims-documents/DC-2026-560771.pdf",
            VERIFIED_B,
        ),
        (
            "EVD-6006",
            SCENARIO_B_CLAIM_ID,
            "CLAIM_FORM",
            "s3://claims-documents/CLM-2026-0002-form.pdf",
            VERIFIED_B,
        ),
        (
            "EVD-6007",
            SCENARIO_B_CLAIM_ID,
            "IDENTITY_PROOF",
            "s3://claims-documents/amara-okafor-id.pdf",
            VERIFIED_B,
        ),
        (
            "EVD-6008",
            SCENARIO_B_CLAIM_ID,
            "BENEFICIARY_PROOF",
            "s3://claims-documents/okafor-marriage-certificate.pdf",
            VERIFIED_B,
        ),
    ]
    for (
        evidence_id,
        claim_id,
        evidence_type,
        document_ref,
        verified_at,
    ) in evidence_rows:
        _insert(
            connection,
            insurance_claim_evidence,
            {
                "id": evidence_id,
                "claim_id": claim_id,
                "evidence_type": evidence_type,
                "document_ref": document_ref,
                "document_hash": f"sha256:{evidence_id.lower()}-verified",
                "status": "VERIFIED",
                "source": "PORTAL",
                "received_at": verified_at,
                "verified_at": verified_at,
                "verified_by": "officer:k.nguyen",
                "notes": None,
            },
        )

    # A restriction is attached to the claim it disputes, so it is written after
    # the claim row exists. (This is also why the domain keeps restrictions on
    # the policy with an optional claim: some holds predate any claim.)
    _insert(
        connection,
        insurance_legal_restriction,
        {
            "id": "LR-9001",
            "policy_id": "POL-1002",
            "claim_id": SCENARIO_B_CLAIM_ID,
            "restriction_type": "COURT_ORDER",
            "description": (
                "Domestic relations court order directing the benefit to the "
                "insured's estate pending probate"
            ),
            "amount": Decimal("400000.00"),
            "status": "ACTIVE",
            "imposed_at": datetime(2026, 5, 2),
            "created_at": datetime(2026, 5, 2),
        },
    )

    return {
        "seeded": True,
        "parties": 5,
        "policies": 2,
        "claims": [SCENARIO_A_CLAIM_ID, SCENARIO_B_CLAIM_ID],
        "scenario_a": "standard natural-death claim, expected AUTO_APPROVED",
        "scenario_b": "accidental death with loan and court order, expected MANUAL_REVIEW",
    }
