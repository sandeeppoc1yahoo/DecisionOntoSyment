# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the ontology layer.

These tests are about *meaning*: a heart attack is a natural death, a suicide
exclusion only bites inside the contestability period, and a certificate for the
wrong person violates a stated constraint.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from auto_ontology.insurance.ontology.reasoner import (
    SATISFIED,
    VIOLATED,
    UnknownCauseError,
    class_ancestors,
    classify_cause,
    is_subclass_of,
    matched_exclusions,
    validate_claim,
)
from auto_ontology.insurance.ontology.vocabulary import (
    EXCLUSIONS,
    LIO,
    ONTOLOGY_VERSION,
)


def test_heart_attack_is_a_natural_death() -> None:
    classification = classify_cause("I21.9")
    assert classification.class_iri == LIO + "CardiovascularDeath"
    assert classification.category == "NATURAL"
    assert is_subclass_of(classification.class_iri, LIO + "NaturalDeath")
    assert is_subclass_of(classification.class_iri, LIO + "CauseOfDeath")
    assert not is_subclass_of(classification.class_iri, LIO + "AccidentalDeath")


def test_inference_chain_is_explained_top_down() -> None:
    classification = classify_cause("I21.9")
    assert classification.inference.startswith("I21.9")
    assert "CardiovascularDeath subClassOf NaturalDeath" in classification.inference
    assert classification.ancestors[0] == LIO + "CardiovascularDeath"
    assert classification.ancestors[-1] == LIO + "CauseOfDeath"


def test_ancestor_closure_terminates() -> None:
    ancestors = class_ancestors(LIO + "PrimaryBeneficiary")
    assert LIO + "Beneficiary" in ancestors
    assert LIO + "Person" in ancestors
    assert LIO + "Party" in ancestors
    assert len(ancestors) == len(set(ancestors))


def test_unknown_cause_is_not_guessed() -> None:
    with pytest.raises(UnknownCauseError):
        classify_cause("Z99.999")


def test_suicide_exclusion_only_applies_inside_contestability() -> None:
    classification = classify_cause("X71")
    exclusion = {
        "id": "EXC-1",
        "exclusion_code": "EX-SUICIDE-2Y",
        "name": "Suicide within contestability",
        "excludes_cause_class": EXCLUSIONS["EX-SUICIDE-2Y"].excludes_class,
        "contestability_only": True,
        "is_active": True,
    }
    inside = matched_exclusions(
        [exclusion], classification, contestability_passed=False
    )
    outside = matched_exclusions(
        [exclusion], classification, contestability_passed=True
    )
    assert [match["code"] for match in inside] == ["EX-SUICIDE-2Y"]
    assert outside == []


def test_aviation_exclusion_matches_by_subsumption() -> None:
    classification = classify_cause("V95.40")
    exclusion = {
        "id": "EXC-2",
        "exclusion_code": "EX-AVIATION",
        "name": "Non-commercial aviation",
        "excludes_cause_class": EXCLUSIONS["EX-AVIATION"].excludes_class,
        "contestability_only": False,
        "is_active": True,
    }
    matches = matched_exclusions(
        [exclusion], classification, contestability_passed=True
    )
    assert len(matches) == 1
    assert "AviationDeath" in matches[0]["reason"]


def test_natural_death_does_not_match_accident_exclusion() -> None:
    classification = classify_cause("I21.9")
    exclusion = {
        "id": "EXC-3",
        "exclusion_code": "EX-AVIATION",
        "name": "Aviation",
        "excludes_cause_class": EXCLUSIONS["EX-AVIATION"].excludes_class,
        "contestability_only": False,
        "is_active": True,
    }
    assert (
        matched_exclusions([exclusion], classification, contestability_passed=True)
        == []
    )


def _facts(**overrides: object) -> dict:
    policy = {
        "insured_person_id": "INS-4001",
        "effective_date": datetime(2021, 6, 15),
        "expiry_date": datetime(2041, 6, 15),
        "status": "IN_FORCE",
        "policy_number": "TL-2021-004821",
    }
    claim = {
        "insured_person_id": "INS-4001",
        "date_of_death": datetime(2026, 6, 14),
        "cause_code": "I21.9",
    }
    certificate = {
        "certificate_number": "DC-1",
        "insured_person_id": "INS-4001",
        "cause_code": "I21.9",
    }
    beneficiaries = [
        {
            "beneficiary_id": "BEN-3001",
            "designation_type": "PRIMARY",
            "allocation_percent": Decimal("100.00"),
            "verification_status": "VERIFIED",
        }
    ]
    facts = {
        "claim": claim,
        "policy": policy,
        "certificate": certificate,
        "beneficiaries": beneficiaries,
        "exclusions": [],
        "competing_claims": [],
        "classification": classify_cause("I21.9"),
        "contestability_passed": True,
    }
    facts.update(overrides)
    return facts


def _status(results: list, code: str) -> str:
    return next(result.status for result in results if result.code == code)


def test_clean_claim_satisfies_every_constraint() -> None:
    results = validate_claim(_facts())
    assert all(result.status == SATISFIED for result in results)


def test_certificate_for_another_person_violates_constraint() -> None:
    facts = _facts()
    facts["certificate"] = {**facts["certificate"], "insured_person_id": "INS-OTHER"}
    results = validate_claim(facts)
    assert _status(results, "C2_CERTIFICATE_MATCH") == VIOLATED


def test_incomplete_allocation_violates_constraint() -> None:
    facts = _facts()
    facts["beneficiaries"] = [
        {**facts["beneficiaries"][0], "allocation_percent": Decimal("50.00")}
    ]
    results = validate_claim(facts)
    assert _status(results, "C5_ALLOCATION_COMPLETE") == VIOLATED


def test_two_primary_beneficiaries_violate_single_primary() -> None:
    first = _facts()["beneficiaries"][0]
    facts = _facts(
        beneficiaries=[
            first,
            {
                **first,
                "beneficiary_id": "BEN-2",
                "allocation_percent": Decimal("50.00"),
            },
        ]
    )
    results = validate_claim(facts)
    assert _status(results, "C6_SINGLE_PRIMARY") == VIOLATED


def test_ontology_version_is_pinned() -> None:
    assert ONTOLOGY_VERSION == "1.0.0"
