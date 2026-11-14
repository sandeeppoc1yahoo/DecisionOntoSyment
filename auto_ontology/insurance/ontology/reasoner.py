# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ontology inference and constraint validation.

Two jobs, both deterministic:

* **Classification** - turn a coded cause of death into the business class it
  belongs to, and report the subsumption chain that justifies it. This is what
  lets ``EX-SUICIDE-2Y`` (which names ``SelfInflictedDeath``) match an ICD-10
  ``X71`` record without any rule knowing what ``X71`` means.
* **Constraint checking** - answer the ontology's stated truth conditions
  against a claim's facts. The decision engine reads these answers; it does not
  re-derive them.

No model is called here, and nothing here reads or writes a database. The
reasoner takes a plain mapping of facts, which is what makes it testable and
what makes a decision reproducible years later from its stored inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from auto_ontology.insurance.ontology.vocabulary import (
    CAUSES,
    CLASS_BY_IRI,
    CONSTRAINTS,
    EXCLUSIONS,
    LIO,
    ONTOLOGY_VERSION,
    PROPERTIES,
    CauseConcept,
    class_parents,
)

SATISFIED = "SATISFIED"
VIOLATED = "VIOLATED"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"


class UnknownCauseError(ValueError):
    """The ICD-10 code has no ontology class, so nothing can be inferred."""


@dataclass(frozen=True)
class CauseClassification:
    """A cause code resolved to its class, with the inference chain."""

    code: str
    description: str
    class_iri: str
    class_label: str
    category: str
    is_self_inflicted: bool
    ancestors: tuple[str, ...]
    inference: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "class_iri": self.class_iri,
            "class_label": self.class_label,
            "category": self.category,
            "is_self_inflicted": self.is_self_inflicted,
            "ancestors": list(self.ancestors),
            "inference": self.inference,
        }


@dataclass(frozen=True)
class OntologyValidation:
    """One constraint, evaluated. ``evidence`` is the reason, not a restatement."""

    code: str
    statement: str
    status: str
    evidence: str
    consequence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint": self.code,
            "statement": self.statement,
            "status": self.status,
            "evidence": self.evidence,
            "consequence": self.consequence,
        }


def class_ancestors(iri: str) -> tuple[str, ...]:
    """Every superclass of *iri*, breadth-first, self first.

    Guards against a cycle in the vocabulary: a malformed ontology should make
    classification fail loudly rather than hang a claims queue.
    """
    ordered: list[str] = []
    frontier = [iri]
    seen: set[str] = set()
    while frontier:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        frontier.extend(class_parents(current))
    return tuple(ordered)


def is_subclass_of(iri: str, ancestor: str) -> bool:
    """True when *iri* is *ancestor* or one of its descendants.

    This is the single inference primitive the exclusion matcher and the graph
    queries share. ``OWL`` would call it ``rdfs:subClassOf`` closure.
    """
    return ancestor in class_ancestors(iri)


def class_label(iri: str) -> str:
    """Human label for a class IRI, falling back to the local name."""
    concept = CLASS_BY_IRI.get(iri)
    if concept is not None:
        return concept.local
    return iri.removeprefix(LIO)


def classify_cause(code: str) -> CauseClassification:
    """Resolve an ICD-10 code to its ontology class.

    Raises :class:`UnknownCauseError` rather than guessing: an unclassified
    cause must become a manual review, never a default of "natural".
    """
    concept: CauseConcept | None = CAUSES.get(code)
    if concept is None:
        raise UnknownCauseError(
            f"Cause of death {code!r} has no ontology class; manual review required"
        )
    ancestors = class_ancestors(concept.class_iri)
    chain = " subClassOf ".join(class_label(iri) for iri in ancestors)
    inference = f"{code} ({concept.description}) => {chain}"
    return CauseClassification(
        code=code,
        description=concept.description,
        class_iri=concept.class_iri,
        class_label=class_label(concept.class_iri),
        category=concept.category,
        is_self_inflicted=concept.is_self_inflicted,
        ancestors=ancestors,
        inference=inference,
    )


def matched_exclusions(
    exclusions: list[dict[str, Any]],
    classification: CauseClassification,
    *,
    contestability_passed: bool,
) -> list[dict[str, Any]]:
    """Exclusions whose excluded class covers the classified cause.

    Three conditions, all necessary:

    1. the exclusion is active;
    2. its excluded class is a superclass of the cause class (or equal to it);
    3. if it is contestability-only, the contestability period has not passed.

    Condition 3 is why the contestability period belongs to the *semantic*
    layer rather than the ontology: it is a contractual time calculation.
    """
    matched: list[dict[str, Any]] = []
    for exclusion in exclusions:
        if not exclusion.get("is_active", True):
            continue
        if exclusion.get("contestability_only") and contestability_passed:
            continue
        target = exclusion.get("excludes_cause_class")
        if not target:
            continue
        if is_subclass_of(classification.class_iri, target):
            matched.append(
                {
                    "exclusion_id": exclusion.get("id"),
                    "code": exclusion.get("exclusion_code"),
                    "name": exclusion.get("name"),
                    "excludes_class": target,
                    "reason": (
                        f"{classification.class_label} is a {class_label(target)} "
                        f"({classification.inference})"
                    ),
                }
            )
    return matched


def validate_claim(facts: dict[str, Any]) -> list[OntologyValidation]:
    """Evaluate every ontology constraint against one claim's facts.

    ``facts`` is the same bundle the decision engine assembles: claim, policy,
    certificate, beneficiaries, exclusions and competing claims. Passing a
    mapping rather than a database handle keeps the ontology layer independent
    of storage and makes a decision's inputs exactly what is persisted.
    """
    claim = facts["claim"]
    policy = facts["policy"]
    certificate = facts.get("certificate")
    beneficiaries: list[dict[str, Any]] = facts.get("beneficiaries", [])
    exclusions: list[dict[str, Any]] = facts.get("exclusions", [])
    competing: list[dict[str, Any]] = facts.get("competing_claims", [])
    classification: CauseClassification = facts["classification"]
    contestability_passed: bool = facts.get("contestability_passed", False)

    results: list[OntologyValidation] = []

    def add(code: str, status: str, evidence: str) -> None:
        constraint = next(c for c in CONSTRAINTS if c.code == code)
        results.append(
            OntologyValidation(
                code=constraint.code,
                statement=constraint.statement,
                status=status,
                evidence=evidence,
                consequence=constraint.consequence,
            )
        )

    # C1 -------------------------------------------------------------------
    if claim["insured_person_id"] == policy["insured_person_id"]:
        add(
            "C1_INSURED_MATCH",
            SATISFIED,
            "Claim and policy both name insured "
            f"{policy['insured_person_id']}; the covered life is unambiguous.",
        )
    else:
        add(
            "C1_INSURED_MATCH",
            VIOLATED,
            f"Claim names {claim['insured_person_id']} but policy covers "
            f"{policy['insured_person_id']}.",
        )

    # C2 -------------------------------------------------------------------
    if certificate is None:
        add("C2_CERTIFICATE_MATCH", UNKNOWN, "No death certificate on the claim.")
    elif certificate["insured_person_id"] == policy["insured_person_id"]:
        add(
            "C2_CERTIFICATE_MATCH",
            SATISFIED,
            f"Certificate {certificate['certificate_number']} certifies the "
            "policy's insured person.",
        )
    else:
        add(
            "C2_CERTIFICATE_MATCH",
            VIOLATED,
            f"Certificate certifies {certificate['insured_person_id']}, not the "
            f"insured {policy['insured_person_id']}.",
        )

    # C3 -------------------------------------------------------------------
    if certificate is None:
        add("C3_CAUSE_CONSISTENCY", UNKNOWN, "No death certificate on the claim.")
    elif certificate["cause_code"] == claim["cause_code"]:
        add(
            "C3_CAUSE_CONSISTENCY",
            SATISFIED,
            f"Both claim and certificate record ICD-10 {claim['cause_code']}.",
        )
    else:
        add(
            "C3_CAUSE_CONSISTENCY",
            VIOLATED,
            f"Claim records {claim['cause_code']} but certificate records "
            f"{certificate['cause_code']}.",
        )

    # C4 -------------------------------------------------------------------
    death_date = claim["date_of_death"]
    if policy["effective_date"] <= death_date <= policy["expiry_date"]:
        add(
            "C4_COVERAGE_PERIOD",
            SATISFIED if policy["status"] == "IN_FORCE" else VIOLATED,
            "Date of death falls inside the coverage window "
            f"({policy['effective_date']:%Y-%m-%d} to "
            f"{policy['expiry_date']:%Y-%m-%d}); policy status is "
            f"{policy['status']}.",
        )
    else:
        add(
            "C4_COVERAGE_PERIOD",
            VIOLATED,
            f"Date of death {death_date:%Y-%m-%d} is outside "
            f"{policy['effective_date']:%Y-%m-%d} to "
            f"{policy['expiry_date']:%Y-%m-%d}.",
        )

    # C5 / C6 / C7 ---------------------------------------------------------
    primary = [b for b in beneficiaries if b.get("designation_type") == "PRIMARY"]
    total = sum((Decimal(str(b["allocation_percent"])) for b in primary), Decimal("0"))
    if not primary:
        add("C5_ALLOCATION_COMPLETE", VIOLATED, "No active primary beneficiary.")
    elif total == Decimal("100.00"):
        add(
            "C5_ALLOCATION_COMPLETE",
            SATISFIED,
            f"Primary allocations total {total}%.",
        )
    else:
        add(
            "C5_ALLOCATION_COMPLETE",
            VIOLATED,
            f"Primary allocations total {total}%, not 100%.",
        )

    verified = [b for b in primary if b.get("verification_status") == "VERIFIED"]
    if len(primary) == 1 and len(verified) == 1:
        add(
            "C6_SINGLE_PRIMARY",
            SATISFIED,
            f"One primary beneficiary ({primary[0]['beneficiary_id']}) and it is "
            "verified.",
        )
    else:
        add(
            "C6_SINGLE_PRIMARY",
            VIOLATED if len(primary) > 1 else UNKNOWN,
            f"{len(primary)} primary beneficiaries, {len(verified)} verified.",
        )

    if not primary:
        add("C7_RELATIONSHIP_VERIFIED", UNKNOWN, "No primary beneficiary.")
    else:
        unverified = [
            b["beneficiary_id"]
            for b in primary
            if b.get("verification_status") != "VERIFIED"
        ]
        add(
            "C7_RELATIONSHIP_VERIFIED",
            SATISFIED if not unverified else VIOLATED,
            (
                "Relationship to insured verified for all primary beneficiaries."
                if not unverified
                else f"Unverified primary beneficiaries: {', '.join(unverified)}."
            ),
        )

    # C8 -------------------------------------------------------------------
    applicable = matched_exclusions(
        exclusions, classification, contestability_passed=contestability_passed
    )
    add(
        "C8_EXCLUSION_FREE",
        SATISFIED if not applicable else VIOLATED,
        (
            "No active exclusion covers "
            f"{classification.class_label} (contestability "
            f"{'passed' if contestability_passed else 'not passed'})."
            if not applicable
            else "Applies: "
            + "; ".join(f"{m['code']} - {m['reason']}" for m in applicable)
        ),
    )

    # C9 -------------------------------------------------------------------
    add(
        "C9_NO_DUPLICATE_CLAIM",
        SATISFIED if not competing else VIOLATED,
        (
            "No competing open claim on this policy."
            if not competing
            else "Competing open claims: "
            + ", ".join(c["claim_number"] for c in competing)
        ),
    )

    return results


def ontology_summary() -> dict[str, Any]:
    """The vocabulary as API- and documentation-friendly data."""
    return {
        "version": ONTOLOGY_VERSION,
        "namespace": LIO,
        "classes": [
            {
                "iri": c.iri,
                "name": c.local,
                "parents": [LIO + p for p in c.parents],
                "description": c.description,
                "constraints": list(c.constraints),
            }
            for c in CLASS_BY_IRI.values()
        ],
        "properties": [
            {
                "iri": p.iri,
                "name": p.local,
                "domain": LIO + p.domain,
                "range": LIO + p.range,
                "cardinality": p.cardinality,
                "description": p.description,
                "transitive": p.is_transitive,
                "symmetric": p.is_symmetric,
            }
            for p in PROPERTIES
        ],
        "constraints": [
            {
                "code": c.code,
                "statement": c.statement,
                "consequence": c.consequence,
            }
            for c in CONSTRAINTS
        ],
        "causes": [
            {
                "code": c.code,
                "description": c.description,
                "class_iri": c.class_iri,
                "category": c.category,
            }
            for c in CAUSES.values()
        ],
        "exclusions": [
            {
                "code": e.code,
                "name": e.name,
                "excludes_class": e.excludes_class,
                "contestability_only": e.contestability_only,
            }
            for e in EXCLUSIONS.values()
        ],
    }
