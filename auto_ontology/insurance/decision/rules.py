# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The decision rule set: deterministic, versioned, individually reportable.

Each rule is a *policy* decision - "a death outside the coverage window is not
payable", "an active legal restriction needs a human" - expressed over the facts
the semantic, ontology and graph layers produced. None of them recomputes a
metric; none of them reads a raw money column. If a rule needs a number, the
semantic layer already published it.

Three severities of consequence:

``DENY``
    The contract does not pay. Only an applicable exclusion and a fully
    encumbered policy qualify: a machine may say "no" only when the contract
    says so.
``REFER``
    A human must look: an ambiguity, missing evidence, contested claim, a large
    payout, or a relationship the graph cannot prove. Everything that is
    *uncertain* lands here rather than defaulting to approval or denial.
``INFORM``
    A fact that changes the explanation or the amount, not eligibility.

Deliberately no model inference in this file. Underwriting judgement encoded in
an LLM would make the same claim decide differently on a re-run, and an insurer
cannot defend that in a complaint or a regulator's sample.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from auto_ontology.insurance.decision.context import DecisionContext
from auto_ontology.insurance.ontology.reasoner import SATISFIED

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

DENY = "DENY"
REFER = "REFER"
INFORM = "INFORM"

RULES_VERSION = "1.0.0"


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    name: str
    category: str
    description: str
    severity: str
    on_fail_action: str
    expression: str
    priority: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "severity": self.severity,
            "on_fail_action": self.on_fail_action,
            "expression": self.expression,
            "priority": self.priority,
            "version": RULES_VERSION,
            "is_active": True,
        }


@dataclass(frozen=True)
class RuleOutcome:
    definition: RuleDefinition
    status: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.definition.id,
            "name": self.definition.name,
            "category": self.definition.category,
            "severity": self.definition.severity,
            "on_fail_action": self.definition.on_fail_action,
            "status": self.status,
            "message": self.message,
            "evidence": self.evidence,
            "version": RULES_VERSION,
        }


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------
RULES: tuple[RuleDefinition, ...] = (
    RuleDefinition(
        id="R01_POLICY_IN_FORCE",
        name="Policy in force on the date of death",
        category="SEMANTIC",
        description=(
            "The policy was in force and the death fell inside the "
            "effective-to-expiry window (semantic definition "
            "policy_in_force_at_death)."
        ),
        severity="CRITICAL",
        on_fail_action=REFER,
        expression="policy_in_force_at_death = true",
        priority=10,
    ),
    RuleDefinition(
        id="R02_CONTESTABILITY_PASSED",
        name="Contestability period passed",
        category="SEMANTIC",
        description=(
            "Cover had run longer than the policy's contestability period, so "
            "the insurer can no longer rescind for misrepresentation and "
            "contestability-only exclusions cannot apply."
        ),
        severity="HIGH",
        on_fail_action=REFER,
        expression="policy_active_days > contestability_period_days",
        priority=20,
    ),
    RuleDefinition(
        id="R03_CAUSE_CLASSIFIED",
        name="Cause of death is classifiable",
        category="ONTOLOGY",
        description=(
            "The certified ICD-10 cause resolves to an ontology class. An "
            "undetermined cause cannot be adjudicated by inference."
        ),
        severity="HIGH",
        on_fail_action=REFER,
        expression="classification.category != 'UNDETERMINED'",
        priority=30,
    ),
    RuleDefinition(
        id="R04_NO_APPLICABLE_EXCLUSION",
        name="No policy exclusion applies",
        category="ONTOLOGY",
        description=(
            "The classified cause is not beneath any active exclusion class. "
            "Contestability-only exclusions are excluded from consideration once "
            "R02 passes."
        ),
        severity="CRITICAL",
        on_fail_action=DENY,
        expression="matched_exclusions = []",
        priority=40,
    ),
    RuleDefinition(
        id="R05_INSURED_IDENTITY_CONSISTENT",
        name="Claim, policy and certificate name the same insured",
        category="KNOWLEDGE_GRAPH",
        description=(
            "Ontology constraints C1 and C2: the claim covers the policy's "
            "insured person and the certificate certifies that same person."
        ),
        severity="CRITICAL",
        on_fail_action=REFER,
        expression="C1_INSURED_MATCH = SATISFIED AND C2_CERTIFICATE_MATCH = SATISFIED",
        priority=50,
    ),
    RuleDefinition(
        id="R06_CAUSE_RECORDS_CONSISTENT",
        name="Claim and certificate record the same cause",
        category="EVIDENCE",
        description=(
            "Ontology constraint C3: the cause on the claim matches the cause on "
            "the certificate, so no later amendment is unaccounted for."
        ),
        severity="HIGH",
        on_fail_action=REFER,
        expression="C3_CAUSE_CONSISTENCY = SATISFIED",
        priority=60,
    ),
    RuleDefinition(
        id="R07_SINGLE_PRIMARY_100_PERCENT",
        name="One primary beneficiary with 100% allocation",
        category="SEMANTIC",
        description=(
            "The designation is complete and unambiguous: exactly one active "
            "primary beneficiary and allocations totalling 100%."
        ),
        severity="CRITICAL",
        on_fail_action=REFER,
        expression=(
            "primary_beneficiary_count = 1 AND total_primary_allocation_percent = 100"
        ),
        priority=70,
    ),
    RuleDefinition(
        id="R08_BENEFICIARY_VERIFIED",
        name="Beneficiary identity and relationship verified",
        category="KNOWLEDGE_GRAPH",
        description=(
            "The primary beneficiary is identity-verified and the graph carries "
            "a verified relationship edge to the insured (for example spouse)."
        ),
        severity="HIGH",
        on_fail_action=REFER,
        expression=(
            "C6_SINGLE_PRIMARY = SATISFIED AND C7_RELATIONSHIP_VERIFIED = SATISFIED"
        ),
        priority=80,
    ),
    RuleDefinition(
        id="R09_NET_BENEFIT_POSITIVE",
        name="Net death benefit is payable",
        category="SEMANTIC",
        description=(
            "Face amount exceeds outstanding policy loans. A fully encumbered "
            "policy has nothing left to pay."
        ),
        severity="CRITICAL",
        on_fail_action=DENY,
        expression="net_death_benefit > 0",
        priority=90,
    ),
    RuleDefinition(
        id="R10_NO_LEGAL_RESTRICTIONS",
        name="No active legal or payment restriction",
        category="KNOWLEDGE_GRAPH",
        description=(
            "No lien, trust, court order, garnishment or fraud hold is active on "
            "the policy. A restriction redirects or blocks payment and always "
            "needs a human."
        ),
        severity="CRITICAL",
        on_fail_action=REFER,
        expression="active_restrictions = 0",
        priority=100,
    ),
    RuleDefinition(
        id="R11_EVIDENCE_COMPLETE",
        name="Required claim evidence verified",
        category="EVIDENCE",
        description=(
            "Every required evidence type for a death claim is present and "
            "verified: certificate, claim form, identity and beneficiary proof."
        ),
        severity="HIGH",
        on_fail_action=REFER,
        expression="required_evidence_types subset of verified_evidence_types",
        priority=110,
    ),
    RuleDefinition(
        id="R12_NO_COMPETING_CLAIMS",
        name="No competing claim on the policy",
        category="KNOWLEDGE_GRAPH",
        description=(
            "No other open claim exists for the same policy. Two claimants for "
            "one death benefit is a dispute."
        ),
        severity="HIGH",
        on_fail_action=REFER,
        expression="competing_claims = 0",
        priority=120,
    ),
    RuleDefinition(
        id="R13_GRAPH_CHAIN_INTACT",
        name="Evidence chain and graph integrity",
        category="KNOWLEDGE_GRAPH",
        description=(
            "A connected path exists from the insured through the policy to the "
            "verified primary beneficiary, and every edge agrees with the "
            "ontology's domain and range."
        ),
        severity="CRITICAL",
        on_fail_action=REFER,
        expression=(
            "path(InsuredPerson, LifeInsurancePolicy, PrimaryBeneficiary) exists "
            "AND graph_violations = []"
        ),
        priority=130,
    ),
    RuleDefinition(
        id="R14_WITHIN_AUTO_APPROVAL_LIMIT",
        name="Payout within straight-through limit",
        category="LIMIT",
        description=(
            "The calculated payout does not exceed the automated approval limit. "
            "Above it a human must authorise the settlement even when every "
            "contractual condition passes."
        ),
        severity="MEDIUM",
        on_fail_action=REFER,
        expression="payout_amount <= auto_approval_limit",
        priority=140,
    ),
    RuleDefinition(
        id="R15_PAYOUT_RECONCILED",
        name="Payout reconciles to benefit and allocation",
        category="SEMANTIC",
        description=(
            "The payout equals net death benefit multiplied by the primary "
            "allocation, to the cent. Guards against a beneficiary split that "
            "does not add up."
        ),
        severity="CRITICAL",
        on_fail_action=REFER,
        expression="payout_amount = net_death_benefit * total_allocation / 100",
        priority=150,
    ),
)

RULES_BY_ID: dict[str, RuleDefinition] = {rule.id: rule for rule in RULES}


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------
def _r01(context: DecisionContext) -> RuleOutcome:
    in_force = context.semantic_bool("policy_in_force_at_death")
    return RuleOutcome(
        RULES_BY_ID["R01_POLICY_IN_FORCE"],
        PASS if in_force else FAIL,
        (
            f"Policy {context.policy['policy_number']} was in force on "
            f"{context.claim['date_of_death']:%Y-%m-%d}."
            if in_force
            else f"Policy status {context.policy['status']}; death "
            f"{context.claim['date_of_death']:%Y-%m-%d} outside cover "
            f"{context.policy['effective_date']:%Y-%m-%d} to "
            f"{context.policy['expiry_date']:%Y-%m-%d}."
        ),
        {
            "policy_status": context.policy["status"],
            "policy_in_force_at_death": in_force,
        },
    )


def _r02(context: DecisionContext) -> RuleOutcome:
    active_days = int(context.semantic.get("policy_active_days"))
    contestability = int(context.semantic.get("contestability_period_days"))
    passed = active_days > contestability
    return RuleOutcome(
        RULES_BY_ID["R02_CONTESTABILITY_PASSED"],
        PASS if passed else FAIL,
        (
            f"{active_days:,} active days exceeds the {contestability:,}-day "
            "contestability period."
            if passed
            else f"{active_days:,} active days is inside the {contestability:,}-day "
            "contestability period."
        ),
        {
            "policy_active_days": active_days,
            "contestability_period_days": contestability,
        },
    )


def _r03(context: DecisionContext) -> RuleOutcome:
    classification = context.classification
    determinable = classification.category != "UNDETERMINED"
    return RuleOutcome(
        RULES_BY_ID["R03_CAUSE_CLASSIFIED"],
        PASS if determinable else FAIL,
        (
            f"{classification.code} classified as {classification.class_label} "
            f"({classification.inference})."
            if determinable
            else f"{classification.code} is an undetermined cause; no inference "
            "about cover is possible."
        ),
        classification.to_dict(),
    )


def _r04(context: DecisionContext) -> RuleOutcome:
    applicable = context.matched_exclusions
    return RuleOutcome(
        RULES_BY_ID["R04_NO_APPLICABLE_EXCLUSION"],
        PASS if not applicable else FAIL,
        (
            f"No active exclusion covers {context.classification.class_label}."
            if not applicable
            else "Applicable exclusion(s): "
            + "; ".join(f"{m['code']} ({m['reason']})" for m in applicable)
        ),
        {
            "cause_class": context.classification.class_iri,
            "matched_exclusions": applicable,
            "exclusions_considered": [e["exclusion_code"] for e in context.exclusions],
            "contestability_passed": context.semantic.get("past_contestability"),
        },
    )


def _r05(context: DecisionContext) -> RuleOutcome:
    c1 = context.ontology_status("C1_INSURED_MATCH")
    c2 = context.ontology_status("C2_CERTIFICATE_MATCH")
    ok = c1 == SATISFIED and c2 == SATISFIED
    return RuleOutcome(
        RULES_BY_ID["R05_INSURED_IDENTITY_CONSISTENT"],
        PASS if ok else FAIL,
        (
            "Claim, policy and death certificate all reference the same insured "
            f"person ({context.policy['insured_person_id']})."
            if ok
            else f"Insured identity inconsistent: C1={c1}, C2={c2}."
        ),
        {"C1_INSURED_MATCH": c1, "C2_CERTIFICATE_MATCH": c2},
    )


def _r06(context: DecisionContext) -> RuleOutcome:
    status = context.ontology_status("C3_CAUSE_CONSISTENCY")
    result = context.ontology_result("C3_CAUSE_CONSISTENCY")
    return RuleOutcome(
        RULES_BY_ID["R06_CAUSE_RECORDS_CONSISTENT"],
        PASS if status == SATISFIED else FAIL,
        result.evidence if result else "Cause consistency could not be evaluated.",
        {"C3_CAUSE_CONSISTENCY": status},
    )


def _r07(context: DecisionContext) -> RuleOutcome:
    count = int(context.semantic.get("primary_beneficiary_count"))
    total = Decimal(str(context.semantic.get("total_primary_allocation_percent")))
    ok = count == 1 and total == Decimal("100.00")
    return RuleOutcome(
        RULES_BY_ID["R07_SINGLE_PRIMARY_100_PERCENT"],
        PASS if ok else FAIL,
        (
            "Exactly one active primary beneficiary with a 100% allocation."
            if ok
            else f"{count} primary beneficiaries totalling {total}% allocation."
        ),
        {
            "primary_beneficiary_count": count,
            "total_primary_allocation_percent": str(total),
        },
    )


def _r08(context: DecisionContext) -> RuleOutcome:
    statuses = {
        "C6_SINGLE_PRIMARY": context.ontology_status("C6_SINGLE_PRIMARY"),
        "C7_RELATIONSHIP_VERIFIED": context.ontology_status("C7_RELATIONSHIP_VERIFIED"),
    }
    ok = all(status == SATISFIED for status in statuses.values())
    primary = [
        d
        for d in context.beneficiary_designations
        if d["designation_type"] == "PRIMARY"
    ]
    name = primary[0]["full_name"] if primary else "none"
    relation = primary[0]["relationship_to_insured"] if primary else "unknown"
    return RuleOutcome(
        RULES_BY_ID["R08_BENEFICIARY_VERIFIED"],
        PASS if ok else FAIL,
        (
            f"{name} is verified as the primary beneficiary and as the insured's "
            f"{relation.lower()}."
            if ok
            else "Beneficiary verification incomplete: "
            + ", ".join(f"{k}={v}" for k, v in statuses.items())
        ),
        {**statuses, "relationship": relation},
    )


def _r09(context: DecisionContext) -> RuleOutcome:
    net = Decimal(str(context.semantic.get("net_death_benefit")))
    loans = Decimal(str(context.semantic.get("outstanding_policy_loan_balance")))
    return RuleOutcome(
        RULES_BY_ID["R09_NET_BENEFIT_POSITIVE"],
        PASS if net > Decimal("0.00") else FAIL,
        (
            f"Net death benefit ${net:,.2f} after ${loans:,.2f} of outstanding loans."
            if net > 0
            else f"Outstanding loans ${loans:,.2f} exhausted the ${net:,.2f} benefit."
        ),
        {"net_death_benefit": str(net), "outstanding_policy_loan_balance": str(loans)},
    )


def _r10(context: DecisionContext) -> RuleOutcome:
    return RuleOutcome(
        RULES_BY_ID["R10_NO_LEGAL_RESTRICTIONS"],
        PASS if not context.restrictions else FAIL,
        (
            "No active lien, trust, court order or fraud hold on the policy."
            if not context.restrictions
            else "Active restrictions: "
            + "; ".join(
                f"{r['restriction_type']} ({r['description']})"
                for r in context.restrictions
            )
        ),
        {
            "restrictions": [
                {
                    "id": r["id"],
                    "type": r["restriction_type"],
                    "status": r["status"],
                }
                for r in context.restrictions
            ]
        },
    )


def _r11(context: DecisionContext) -> RuleOutcome:
    missing = context.missing_evidence
    return RuleOutcome(
        RULES_BY_ID["R11_EVIDENCE_COMPLETE"],
        PASS if not missing else FAIL,
        (
            "All required evidence types verified: "
            + ", ".join(context.settings.required_evidence_types)
            if not missing
            else "Missing verified evidence: " + ", ".join(missing)
        ),
        {
            "verified": sorted(context.verified_evidence_types),
            "missing": missing,
        },
    )


def _r12(context: DecisionContext) -> RuleOutcome:
    competing = context.competing_claims
    return RuleOutcome(
        RULES_BY_ID["R12_NO_COMPETING_CLAIMS"],
        PASS if not competing else FAIL,
        (
            "No other open claim exists for this policy."
            if not competing
            else "Competing claims: "
            + ", ".join(f"{c['claim_number']} ({c['status']})" for c in competing)
        ),
        {"competing_claims": [c["claim_number"] for c in competing]},
    )


def _r13(context: DecisionContext) -> RuleOutcome:
    chain_ok = context.evidence_chain is not None
    clean = not context.graph_violations
    ok = chain_ok and clean
    return RuleOutcome(
        RULES_BY_ID["R13_GRAPH_CHAIN_INTACT"],
        PASS if ok else FAIL,
        (
            f"Verified path: {context.evidence_chain_description()}."
            if ok
            else (
                "Graph chain invalid: no insured->policy->primary-beneficiary path."
                if not chain_ok
                else f"{len(context.graph_violations)} ontology domain/range violation(s)."
            )
        ),
        {
            "path": context.evidence_chain.to_dict()
            if context.evidence_chain
            else None,
            "violations": context.graph_violations,
        },
    )


def _r14(context: DecisionContext) -> RuleOutcome:
    payout = Decimal(str(context.semantic.get("payout_amount")))
    limit = context.settings.auto_approval_limit
    return RuleOutcome(
        RULES_BY_ID["R14_WITHIN_AUTO_APPROVAL_LIMIT"],
        PASS if payout <= limit else FAIL,
        (
            f"Payout ${payout:,.2f} is within the ${limit:,.2f} straight-through limit."
            if payout <= limit
            else f"Payout ${payout:,.2f} exceeds the ${limit:,.2f} limit; human "
            "authorisation required."
        ),
        {"payout_amount": str(payout), "auto_approval_limit": str(limit)},
    )


def _r15(context: DecisionContext) -> RuleOutcome:
    payout = Decimal(str(context.semantic.get("payout_amount")))
    net = Decimal(str(context.semantic.get("net_death_benefit")))
    total = Decimal(str(context.semantic.get("total_primary_allocation_percent")))
    expected = (net * total / Decimal("100")).quantize(Decimal("0.01"))
    ok = payout == expected
    return RuleOutcome(
        RULES_BY_ID["R15_PAYOUT_RECONCILED"],
        PASS if ok else FAIL,
        (
            f"Payout ${payout:,.2f} reconciles to ${net:,.2f} x {total}%."
            if ok
            else f"Payout ${payout:,.2f} does not reconcile to ${expected:,.2f}."
        ),
        {
            "payout_amount": str(payout),
            "expected": str(expected),
            "net_death_benefit": str(net),
            "total_allocation_percent": str(total),
        },
    )


EVALUATORS: dict[str, Callable[[DecisionContext], RuleOutcome]] = {
    "R01_POLICY_IN_FORCE": _r01,
    "R02_CONTESTABILITY_PASSED": _r02,
    "R03_CAUSE_CLASSIFIED": _r03,
    "R04_NO_APPLICABLE_EXCLUSION": _r04,
    "R05_INSURED_IDENTITY_CONSISTENT": _r05,
    "R06_CAUSE_RECORDS_CONSISTENT": _r06,
    "R07_SINGLE_PRIMARY_100_PERCENT": _r07,
    "R08_BENEFICIARY_VERIFIED": _r08,
    "R09_NET_BENEFIT_POSITIVE": _r09,
    "R10_NO_LEGAL_RESTRICTIONS": _r10,
    "R11_EVIDENCE_COMPLETE": _r11,
    "R12_NO_COMPETING_CLAIMS": _r12,
    "R13_GRAPH_CHAIN_INTACT": _r13,
    "R14_WITHIN_AUTO_APPROVAL_LIMIT": _r14,
    "R15_PAYOUT_RECONCILED": _r15,
}


def evaluate_rules(context: DecisionContext) -> list[RuleOutcome]:
    """Run every rule in priority order.

    A rule whose definition has no evaluator is a deployment error, not a
    skipped check: silently skipping would let an unadjudicated claim through.
    """
    missing = [rule.id for rule in RULES if rule.id not in EVALUATORS]
    if missing:
        raise RuntimeError(f"Decision rules without evaluators: {missing}")
    return [
        EVALUATORS[rule.id](context) for rule in sorted(RULES, key=lambda r: r.priority)
    ]
