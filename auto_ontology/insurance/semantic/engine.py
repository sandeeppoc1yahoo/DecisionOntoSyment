# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The semantic layer's calculation engine.

One number, one implementation. The decision engine reads these values; it never
recomputes "net death benefit" from a column, and neither does the payment
gateway. That single rule is what makes the payout on a decision, the amount on
a payment and the number in an audit report agree by construction.

Every value is returned with the definition it implements, the formula, the
version, the actual inputs, and a display string. That is the difference between
a calculation and an *explainable* calculation: a reviewer can check the
arithmetic without opening the database.

Deterministic by design. Dates, money and percentages are the last place an LLM
should be doing arithmetic on a claim, so none of them are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from auto_ontology.insurance import repository
from auto_ontology.insurance.types import as_money
from auto_ontology.insurance.errors import ClaimNotFoundError, PolicyNotFoundError
from auto_ontology.insurance.semantic.definitions import (
    DEFINITIONS_BY_NAME,
    SemanticDefinition,
    validate_registry,
)

validate_registry()

CURRENCY_SYMBOL = "$"


@dataclass(frozen=True)
class SemanticValue:
    """One evaluated definition."""

    name: str
    display_name: str
    value: Any
    display_value: str
    unit: str
    definition_kind: str
    formula: str
    version: str
    inputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "value": _jsonable(self.value),
            "display_value": self.display_value,
            "unit": self.unit,
            "definition_kind": self.definition_kind,
            "formula": self.formula,
            "definition_version": self.version,
            "inputs": self.inputs,
        }


@dataclass
class SemanticResult:
    """Every certified value for one claim, plus the facts they were built on."""

    claim_id: str
    policy_id: str
    values: dict[str, SemanticValue] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        return self.values[name].value

    def __getitem__(self, name: str) -> SemanticValue:
        return self.values[name]

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "policy_id": self.policy_id,
            "definitions_evaluated": len(self.values),
            "values": [value.to_dict() for value in self.values.values()],
        }


def _jsonable(value: Any) -> Any:
    """Recursively make a value JSON-safe for the decision payload.

    Recursive, not top-level: ``death_benefit_by_beneficiary`` returns a list of
    dicts that carry a Decimal amount each, and the decision row is a JSON
    column.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _format_money(amount: Decimal) -> str:
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


def _format_days(days: int) -> str:
    return f"{days:,} days"


def _make(
    name: str,
    value: Any,
    display: str,
    inputs: dict[str, Any] | None = None,
) -> SemanticValue:
    definition: SemanticDefinition = DEFINITIONS_BY_NAME[name]
    return SemanticValue(
        name=definition.name,
        display_name=definition.display_name,
        value=value,
        display_value=display,
        unit=definition.unit,
        definition_kind=definition.definition_kind,
        formula=definition.formula,
        version=definition.version,
        inputs=inputs or {},
    )


class SemanticEngine:
    """Evaluates the certified definitions for a claim.

    ``evaluate`` is the only public entry point. It loads the claim's contractual
    facts, computes the definitions in registry order (which
    ``validate_registry`` guarantees is dependency order), and returns them
    together with the raw facts the decision engine needs for rule predicates.
    """

    def evaluate(self, connection: Any, claim_id: str) -> SemanticResult:
        claim = repository.get_claim(connection, claim_id)
        if claim is None:
            raise ClaimNotFoundError(claim_id)
        policy = repository.get_policy(connection, claim["policy_id"])
        if policy is None:
            raise PolicyNotFoundError(claim["policy_id"])

        beneficiaries = repository.get_beneficiaries(connection, policy["id"])
        loans = repository.get_loans(connection, policy["id"])

        result = SemanticResult(claim_id=claim_id, policy_id=policy["id"])

        effective: datetime = policy["effective_date"]
        expiry: datetime = policy["expiry_date"]
        date_of_death: datetime = claim["date_of_death"]
        active_days = (date_of_death.date() - effective.date()).days
        contestability_days = int(policy["contestability_days"])
        face_amount = as_money(policy["face_amount"])
        outstanding = as_money(
            sum(
                (
                    loan["outstanding_balance"]
                    for loan in loans
                    if loan["status"] == "OUTSTANDING"
                ),
                Decimal("0.00"),
            )
        )
        net_benefit = as_money(face_amount - outstanding)

        primary = [b for b in beneficiaries if b["designation_type"] == "PRIMARY"]
        primary_count = len(primary)
        total_allocation = sum(
            (Decimal(str(b["allocation_percent"])) for b in primary), Decimal("0.00")
        )
        payout = as_money(net_benefit * total_allocation / Decimal("100"))

        in_window = effective <= date_of_death <= expiry
        in_force = policy["status"] == "IN_FORCE"
        policy_in_force = in_force and in_window

        if policy_in_force:
            eligibility = "ELIGIBLE"
            eligibility_reason = (
                f"Policy was {policy['status']} and the death on "
                f"{date_of_death:%Y-%m-%d} fell inside cover "
                f"{effective:%Y-%m-%d} to {expiry:%Y-%m-%d}."
            )
        elif not in_window:
            eligibility = "NOT_COVERED"
            eligibility_reason = (
                f"Death on {date_of_death:%Y-%m-%d} is outside the coverage "
                f"window {effective:%Y-%m-%d} to {expiry:%Y-%m-%d}."
            )
        else:
            eligibility = "POLICY_NOT_IN_FORCE"
            eligibility_reason = f"Policy status at death was {policy['status']}."

        allocations = self._apportion(net_benefit, primary)

        result.values["policy_effective_date"] = _make(
            "policy_effective_date",
            effective.date().isoformat(),
            effective.date().isoformat(),
            {"policy.id": policy["id"]},
        )
        result.values["policy_expiry_date"] = _make(
            "policy_expiry_date",
            expiry.date().isoformat(),
            expiry.date().isoformat(),
            {"policy.id": policy["id"]},
        )
        result.values["policy_active_days"] = _make(
            "policy_active_days",
            active_days,
            _format_days(active_days),
            {
                "policy_effective_date": effective.date().isoformat(),
                "date_of_death": date_of_death.date().isoformat(),
            },
        )
        result.values["contestability_period_days"] = _make(
            "contestability_period_days",
            contestability_days,
            _format_days(contestability_days),
            {"policy.contestability_days": contestability_days},
        )
        result.values["past_contestability"] = _make(
            "past_contestability",
            active_days > contestability_days,
            "true" if active_days > contestability_days else "false",
            {
                "policy_active_days": active_days,
                "contestability_period_days": contestability_days,
            },
        )
        result.values["policy_face_amount"] = _make(
            "policy_face_amount",
            face_amount,
            _format_money(face_amount),
            {"policy.face_amount": str(face_amount)},
        )
        result.values["outstanding_policy_loan_balance"] = _make(
            "outstanding_policy_loan_balance",
            outstanding,
            _format_money(outstanding),
            {
                "loans_considered": len(loans),
                "loans_outstanding": sum(
                    1 for loan in loans if loan["status"] == "OUTSTANDING"
                ),
            },
        )
        result.values["net_death_benefit"] = _make(
            "net_death_benefit",
            net_benefit,
            _format_money(net_benefit),
            {
                "policy_face_amount": str(face_amount),
                "outstanding_loans": str(outstanding),
            },
        )
        result.values["primary_beneficiary_count"] = _make(
            "primary_beneficiary_count",
            primary_count,
            str(primary_count),
            {"primary_beneficiaries": [b["beneficiary_id"] for b in primary]},
        )
        result.values["total_primary_allocation_percent"] = _make(
            "total_primary_allocation_percent",
            total_allocation,
            f"{total_allocation:.2f}%",
            {"allocations": [str(b["allocation_percent"]) for b in primary]},
        )
        result.values["payout_amount"] = _make(
            "payout_amount",
            payout,
            _format_money(payout),
            {
                "net_death_benefit": str(net_benefit),
                "total_primary_allocation_percent": str(total_allocation),
            },
        )
        result.values["policy_in_force_at_death"] = _make(
            "policy_in_force_at_death",
            policy_in_force,
            "true" if policy_in_force else "false",
            {"policy.status": policy["status"], "date_in_coverage_window": in_window},
        )
        result.values["claim_eligibility"] = _make(
            "claim_eligibility",
            eligibility,
            eligibility,
            {"reason": eligibility_reason},
        )
        result.values["death_benefit_by_beneficiary"] = _make(
            "death_benefit_by_beneficiary",
            allocations,
            "; ".join(f"{a['name']} {_format_money(a['_amount'])}" for a in allocations)
            or "no beneficiary",
            {"net_death_benefit": str(net_benefit)},
        )

        result.facts = {
            "claim": claim,
            "policy": policy,
            "beneficiaries": beneficiaries,
            "loans": loans,
            "active_days": active_days,
            "contestability_days": contestability_days,
            "contestability_passed": active_days > contestability_days,
            "face_amount": face_amount,
            "outstanding_loans": outstanding,
            "net_death_benefit": net_benefit,
            "primary_beneficiaries": primary,
            "primary_count": primary_count,
            "total_primary_allocation": total_allocation,
            "payout_amount": payout,
            "policy_in_force": policy_in_force,
            "eligibility": eligibility,
            "allocations": allocations,
        }
        return result

    @staticmethod
    def _apportion(
        net_benefit: Decimal, primary: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Split a benefit by allocation, assigning rounding residual to the last.

        Rounding down each share and giving the remainder to the final
        beneficiary keeps the parts summing exactly to the whole. Paying out
        $249,999.99 because three shares each rounded down is a defect that
        reaches a customer's bank account.
        """
        if not primary:
            return []
        shares: list[dict[str, Any]] = []
        allocated = Decimal("0.00")
        for index, beneficiary in enumerate(primary):
            percent = Decimal(str(beneficiary["allocation_percent"]))
            if index == len(primary) - 1:
                amount = as_money(net_benefit - allocated)
            else:
                amount = (net_benefit * percent / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
                allocated += amount
            shares.append(
                {
                    "beneficiary_id": beneficiary["beneficiary_id"],
                    "party_id": beneficiary["party_id"],
                    "name": beneficiary.get("full_name")
                    or beneficiary["beneficiary_id"],
                    "allocation_percent": str(percent),
                    "amount": str(amount),
                    "_amount": amount,
                }
            )
        return shares
