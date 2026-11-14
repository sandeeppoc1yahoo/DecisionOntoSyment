# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The life insurance ontology: classes, properties, and the inference rules.

This is the T-Box - the *terminology* - and it is deliberately code, not prose
in a document. A class hierarchy that only exists in a PDF cannot stop a rule
from treating a heart attack as an accident, and a relationship that only exists
in a diagram cannot be checked against the data.

What it buys the decision engine:

* **Subsumption.** ``lio:MyocardialInfarction subClassOf lio:CardiovascularDeath  subClassOf
  lio:NaturalDeath subClassOf lio:CauseOfDeath``. A rule written against
  ``NaturalDeath`` then applies to every cause beneath it, so adding a new
  cardiac ICD code does not require editing any rule.
* **Closed-world exclusion matching.** An exclusion names a class, not a
  keyword. ``EX-SUICIDE-2Y`` excludes ``lio:SelfInflictedDeath``, so an
  ICD-10 ``X71`` death matches by inference rather than by string search.
* **Cardinality and domain/range.** ``coveredBy`` runs InsuredPerson ->
  LifeInsurancePolicy. A graph edge in the other direction is a modelling bug,
  and the graph validator can say so.
* **Shareable meaning.** The same class IRIs are used by the semantic layer
  (``exclusion_match``), the graph builder (node typing) and the decision rules,
  which is what lets three layers agree without restating each other.

The vocabulary is versioned. A decision records the ontology version it was
adjudicated against, because "heart attack is a natural death" is a business
assertion that could change, and an audit must know which version applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Namespace for the life insurance ontology. Local names are what the API and
#: the graph display; the prefixed IRI is what is persisted and reasoned over.
LIO = "lio:"
ONTOLOGY_VERSION = "1.0.0"


@dataclass(frozen=True)
class OntologyClass:
    """A business concept.

    ``parents`` is a tuple because the domain genuinely has multiple
    inheritance: a spouse is a person *and* a beneficiary role holder.
    """

    local: str
    parents: tuple[str, ...]
    description: str
    constraints: tuple[str, ...] = field(default_factory=tuple)

    @property
    def iri(self) -> str:
        return LIO + self.local


@dataclass(frozen=True)
class OntologyProperty:
    """A relationship type, with the classes it may connect."""

    local: str
    domain: str
    range: str
    description: str
    cardinality: str = "0..*"
    is_transitive: bool = False
    is_symmetric: bool = False

    @property
    def iri(self) -> str:
        return LIO + self.local


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------
CLASSES: tuple[OntologyClass, ...] = (
    # Parties and roles -----------------------------------------------------
    OntologyClass(
        "Party",
        (),
        "Any legal or natural person known to the insurer.",
        ("A party is identified by exactly one party record; roles reference it.",),
    ),
    OntologyClass("Person", ("Party",), "A natural person."),
    OntologyClass(
        "Policyholder",
        ("Party",),
        "The party that owns the contract and pays premium (may be an organisation).",
        ("A policy has exactly one policyholder.",),
    ),
    OntologyClass(
        "InsuredPerson",
        ("Person",),
        "The person whose life is covered.",
        ("A person may be the insured on more than one policy.",),
    ),
    OntologyClass(
        "Claimant",
        ("Person",),
        "The party that submits the claim, usually the beneficiary.",
    ),
    OntologyClass(
        "Beneficiary",
        ("Person",),
        "A party designated to receive the death benefit.",
        (
            "A beneficiary designation is policy-specific.",
            "A beneficiary allocation is a percentage of the net death benefit.",
        ),
    ),
    OntologyClass(
        "PrimaryBeneficiary",
        ("Beneficiary",),
        "First-in-line beneficiary, paid before any contingent beneficiary.",
        ("Active primary allocations must total 100%.",),
    ),
    OntologyClass(
        "ContingentBeneficiary",
        ("Beneficiary",),
        "Beneficiary paid only if no primary beneficiary survives.",
    ),
    OntologyClass(
        "Spouse",
        ("Person",),
        "A person married to the insured at the date of death.",
        ("Spousal status is evidenced, not assumed.",),
    ),
    OntologyClass("Child", ("Person",), "A child of the insured."),
    # Contract --------------------------------------------------------------
    OntologyClass(
        "LifeInsurancePolicy",
        (),
        "A contract that pays a face amount on the death of the insured.",
        (
            "Exactly one insured person.",
            "Coverage runs from effective date to expiry date inclusive of issue.",
            "A contestability period applies from the effective date.",
        ),
    ),
    OntologyClass(
        "PolicyLoan",
        (),
        "A loan advanced against the policy's cash value.",
        ("An outstanding loan reduces the net death benefit.",),
    ),
    OntologyClass(
        "PolicyExclusion",
        (),
        "A contractual carve-out that removes a cause of death from cover.",
        ("An exclusion names the ontology class of death it excludes.",),
    ),
    OntologyClass(
        "ContestabilityPeriod",
        (),
        "The period in which the insurer may contest the contract.",
        ("Lasts the policy's contestability_days from the effective date.",),
    ),
    OntologyClass(
        "LegalRestriction",
        (),
        "A lien, trust, court order or fraud hold affecting payment.",
        ("An active restriction blocks straight-through payment.",),
    ),
    # Claim -----------------------------------------------------------------
    OntologyClass(
        "InsuranceClaim",
        (),
        "A request for the death benefit under a policy.",
        (
            "Relates to exactly one policy and one insured person.",
            "Requires verified evidence before payment.",
        ),
    ),
    OntologyClass(
        "DeathCertificate",
        (),
        "Official record of the fact and cause of death.",
        ("Must certify the death of the policy's insured person.",),
    ),
    OntologyClass(
        "CauseOfDeath",
        (),
        "The medically certified cause of death (ICD-10 coded).",
    ),
    OntologyClass(
        "NaturalDeath",
        ("CauseOfDeath",),
        "Death from disease or internal bodily failure.",
    ),
    OntologyClass(
        "CardiovascularDeath",
        ("NaturalDeath",),
        "Natural death of cardiac or vascular origin, including heart attack.",
    ),
    OntologyClass(
        "MalignantNeoplasm",
        ("NaturalDeath",),
        "Death from cancer.",
    ),
    OntologyClass(
        "RespiratoryDeath",
        ("NaturalDeath",),
        "Death from respiratory disease.",
    ),
    OntologyClass(
        "AccidentalDeath",
        ("CauseOfDeath",),
        "Death caused by an external, unintentional event.",
    ),
    OntologyClass(
        "AviationDeath",
        ("AccidentalDeath",),
        "Death in an aviation event, excluding licensed common carriers.",
    ),
    OntologyClass(
        "WarOrHostilitiesDeath",
        ("CauseOfDeath",),
        "Death caused by war, invasion or hostilities.",
    ),
    OntologyClass(
        "IllegalActDeath",
        ("CauseOfDeath",),
        "Death while committing or attempting a criminal act.",
    ),
    OntologyClass(
        "SelfInflictedDeath",
        ("CauseOfDeath",),
        "Death by intentional self-harm, whether or not the person intended to die.",
    ),
    OntologyClass(
        "UndeterminedDeath",
        ("CauseOfDeath",),
        "A death whose manner could not be established; always needs review.",
    ),
    OntologyClass(
        "ClaimEvidence",
        (),
        "A document or record supporting a claim.",
        ("Evidence is verified by a named person or system before use.",),
    ),
    OntologyClass(
        "ClaimApproval",
        ("InsuranceClaim",),
        "A decision that the claim is payable, in whole or in part.",
        ("Produced only by the decision engine or an authorised reviewer.",),
    ),
    OntologyClass(
        "ClaimPayment",
        (),
        "A disbursement of an approved claim to a beneficiary.",
        ("Requires an approved decision and a verified beneficiary.",),
    ),
)

# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
PROPERTIES: tuple[OntologyProperty, ...] = (
    OntologyProperty(
        "ownsPolicy",
        "Policyholder",
        "LifeInsurancePolicy",
        "The policyholder owns the contract.",
        "1..1",
    ),
    OntologyProperty(
        "coveredBy",
        "InsuredPerson",
        "LifeInsurancePolicy",
        "The insured person's life is covered by the policy.",
    ),
    OntologyProperty(
        "hasBeneficiary",
        "LifeInsurancePolicy",
        "Beneficiary",
        "The policy designates this beneficiary.",
    ),
    OntologyProperty(
        "beneficiaryOf",
        "Beneficiary",
        "LifeInsurancePolicy",
        "Inverse of hasBeneficiary.",
    ),
    OntologyProperty(
        "hasRelationshipToInsured",
        "Beneficiary",
        "Person",
        "The beneficiary's stated relationship to the insured.",
    ),
    OntologyProperty(
        "spouseOf",
        "Spouse",
        "Person",
        "Marital relationship, symmetric at the date of death.",
        is_symmetric=True,
    ),
    OntologyProperty(
        "hasClaim",
        "LifeInsurancePolicy",
        "InsuranceClaim",
        "A claim has been submitted under the policy.",
    ),
    OntologyProperty(
        "submittedBy",
        "InsuranceClaim",
        "Claimant",
        "The party that submitted the claim.",
    ),
    OntologyProperty(
        "relatesToInsured",
        "InsuranceClaim",
        "InsuredPerson",
        "The insured person whose death is claimed.",
    ),
    OntologyProperty(
        "hasEvidence",
        "InsuranceClaim",
        "ClaimEvidence",
        "Evidence supplied in support of the claim.",
    ),
    OntologyProperty(
        "evidencedByCertificate",
        "InsuranceClaim",
        "DeathCertificate",
        "The death certificate attached to the claim.",
    ),
    OntologyProperty(
        "certifiesDeathOf",
        "DeathCertificate",
        "InsuredPerson",
        "The person whose death the certificate records.",
        "1..1",
    ),
    OntologyProperty(
        "hasCauseOfDeath",
        "DeathCertificate",
        "CauseOfDeath",
        "The certified cause of death.",
        "1..1",
    ),
    OntologyProperty(
        "hasExclusion",
        "LifeInsurancePolicy",
        "PolicyExclusion",
        "A carve-out in the contract.",
    ),
    OntologyProperty(
        "excludesCause",
        "PolicyExclusion",
        "CauseOfDeath",
        "The class of death the exclusion removes from cover.",
    ),
    OntologyProperty(
        "hasLoan",
        "LifeInsurancePolicy",
        "PolicyLoan",
        "A loan advanced against the policy.",
    ),
    OntologyProperty(
        "hasRestriction",
        "LifeInsurancePolicy",
        "LegalRestriction",
        "A legal or payment restriction on the policy.",
    ),
    OntologyProperty(
        "resultedIn",
        "InsuranceClaim",
        "ClaimApproval",
        "The approval produced by adjudication.",
    ),
    OntologyProperty(
        "paidBy",
        "ClaimApproval",
        "ClaimPayment",
        "The payment that settles the approved claim.",
    ),
    OntologyProperty(
        "sameAs",
        "Party",
        "Party",
        (
            "Identity link between two role nodes that are the same legal person "
            "(the claimant who is also the primary beneficiary)."
        ),
        is_symmetric=True,
        is_transitive=True,
    ),
)


# ---------------------------------------------------------------------------
# Individuals: ICD-10 causes of death
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CauseConcept:
    """An ICD-10 code mapped to the ontology class it belongs to.

    This mapping *is* the medical domain knowledge. It is deliberately a table
    with a version, because a coder adding ``I21.4`` should extend cover
    classification without touching a decision rule.
    """

    code: str
    description: str
    class_iri: str
    category: str
    is_self_inflicted: bool = False
    icd10_chapter: str = ""

    @property
    def natural(self) -> bool:
        return self.category == "NATURAL"


CAUSES: dict[str, CauseConcept] = {
    "I21.9": CauseConcept(
        code="I21.9",
        description="Acute myocardial infarction, unspecified (heart attack)",
        class_iri=LIO + "CardiovascularDeath",
        category="NATURAL",
        icd10_chapter="IX",
    ),
    "I25.10": CauseConcept(
        code="I25.10",
        description="Atherosclerotic heart disease of native coronary artery",
        class_iri=LIO + "CardiovascularDeath",
        category="NATURAL",
        icd10_chapter="IX",
    ),
    "I64": CauseConcept(
        code="I64",
        description="Stroke, not specified as haemorrhage or infarction",
        class_iri=LIO + "CardiovascularDeath",
        category="NATURAL",
        icd10_chapter="IX",
    ),
    "C34.90": CauseConcept(
        code="C34.90",
        description="Malignant neoplasm of unspecified part of bronchus or lung",
        class_iri=LIO + "MalignantNeoplasm",
        category="NATURAL",
        icd10_chapter="II",
    ),
    "J18.9": CauseConcept(
        code="J18.9",
        description="Pneumonia, unspecified organism",
        class_iri=LIO + "RespiratoryDeath",
        category="NATURAL",
        icd10_chapter="X",
    ),
    "V89.2": CauseConcept(
        code="V89.2",
        description="Person injured in unspecified motor-vehicle accident",
        class_iri=LIO + "AccidentalDeath",
        category="ACCIDENTAL",
        icd10_chapter="XX",
    ),
    "W19.XXXA": CauseConcept(
        code="W19.XXXA",
        description="Unspecified fall, initial encounter",
        class_iri=LIO + "AccidentalDeath",
        category="ACCIDENTAL",
        icd10_chapter="XX",
    ),
    "V95.40": CauseConcept(
        code="V95.40",
        description="Unspecified powered aircraft accident injuring occupant",
        class_iri=LIO + "AviationDeath",
        category="ACCIDENTAL",
        icd10_chapter="XX",
    ),
    "X71": CauseConcept(
        code="X71",
        description="Intentional self-harm by drowning and submersion",
        class_iri=LIO + "SelfInflictedDeath",
        category="SELF_INFLICTED",
        is_self_inflicted=True,
        icd10_chapter="XX",
    ),
    "R99": CauseConcept(
        code="R99",
        description="Ill-defined and unknown cause of mortality",
        class_iri=LIO + "UndeterminedDeath",
        category="UNDETERMINED",
        icd10_chapter="XVIII",
    ),
}

#: The class a cause code is an instance of, used to type graph nodes.
NATURAL_CLASSES = (
    "lio:NaturalDeath",
    "lio:CardiovascularDeath",
    "lio:MalignantNeoplasm",
    "lio:RespiratoryDeath",
)


# ---------------------------------------------------------------------------
# Policy exclusions as class-level carve-outs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExclusionConcept:
    """A standard exclusion and the ontology class it removes from cover."""

    code: str
    name: str
    description: str
    excludes_class: str
    contestability_only: bool = False


EXCLUSIONS: dict[str, ExclusionConcept] = {
    "EX-SUICIDE-2Y": ExclusionConcept(
        code="EX-SUICIDE-2Y",
        name="Suicide within contestability period",
        description=(
            "Death by intentional self-harm within the contestability period is "
            "not covered; the insurer instead returns premium paid."
        ),
        excludes_class="lio:SelfInflictedDeath",
        contestability_only=True,
    ),
    "EX-WAR": ExclusionConcept(
        code="EX-WAR",
        name="War and hostilities",
        description="Death caused by declared or undeclared war or hostilities.",
        excludes_class="lio:WarOrHostilitiesDeath",
    ),
    "EX-AVIATION": ExclusionConcept(
        code="EX-AVIATION",
        name="Non-commercial aviation",
        description=(
            "Death in aviation other than as a fare-paying passenger on a "
            "licensed common carrier."
        ),
        excludes_class="lio:AviationDeath",
    ),
    "EX-ILLEGAL-ACT": ExclusionConcept(
        code="EX-ILLEGAL-ACT",
        name="Criminal act",
        description="Death while committing or attempting a felony.",
        excludes_class="lio:IllegalActDeath",
    ),
}


# ---------------------------------------------------------------------------
# Constraints: the OWL-style rules the validator enforces
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OntologyConstraint:
    """A stated constraint, with the business consequence of violating it."""

    code: str
    statement: str
    consequence: str


CONSTRAINTS: tuple[OntologyConstraint, ...] = (
    OntologyConstraint(
        "C1_INSURED_MATCH",
        "The claim's insured person and the policy's insured person are the same.",
        "A claim for a different person is void and must be manually reviewed.",
    ),
    OntologyConstraint(
        "C2_CERTIFICATE_MATCH",
        "The death certificate certifies the death of the policy's insured person.",
        "A mismatched certificate does not evidence this claim.",
    ),
    OntologyConstraint(
        "C3_CAUSE_CONSISTENCY",
        "The claim's stated cause and the certified cause are the same code.",
        "Differing causes mean one record was amended; a human must reconcile it.",
    ),
    OntologyConstraint(
        "C4_COVERAGE_PERIOD",
        "The policy was in force on the date of death, within effective and expiry.",
        "Death outside the coverage window is not a covered event.",
    ),
    OntologyConstraint(
        "C5_ALLOCATION_COMPLETE",
        "Active primary beneficiary allocations total exactly 100%.",
        "An incomplete designation cannot be paid straight through.",
    ),
    OntologyConstraint(
        "C6_SINGLE_PRIMARY",
        "Exactly one verified primary beneficiary exists for fast-track payment.",
        "Multiple primaries require apportionment and human review.",
    ),
    OntologyConstraint(
        "C7_RELATIONSHIP_VERIFIED",
        "The beneficiary's relationship to the insured is verified.",
        "An unverified relationship could defeat the designation.",
    ),
    OntologyConstraint(
        "C8_EXCLUSION_FREE",
        "No active policy exclusion applies to the classified cause of death.",
        "An applicable exclusion removes the death from cover.",
    ),
    OntologyConstraint(
        "C9_NO_DUPLICATE_CLAIM",
        "No other open claim exists for the same policy and insured.",
        "Competing claims indicate a dispute and require review.",
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------
CLASS_BY_IRI: dict[str, OntologyClass] = {c.iri: c for c in CLASSES}
PROPERTY_BY_IRI: dict[str, OntologyProperty] = {p.iri: p for p in PROPERTIES}


def class_parents(iri: str) -> tuple[str, ...]:
    """Direct superclasses of *iri* (empty for a root or unknown class)."""
    concept = CLASS_BY_IRI.get(iri)
    return tuple(LIO + parent for parent in concept.parents) if concept else ()
