# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Runtime settings for the insurance domain.

Kept in one frozen object rather than scattered ``os.environ`` reads so every
threshold that changes a payout is discoverable, testable and reportable in the
decision's audit payload. A decision that used a different limit from the one
on the policy schedule is an audit finding, not a configuration detail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

#: Where the domain tables live. SQLite by default so the demo and the test
#: suite run with no infrastructure; point this at the platform's Postgres
#: (``postgresql+psycopg://...``) in a deployment so the claim tables sit beside
#: the Auto-Ontology catalog they were modelled from.
DEFAULT_DB_URL = "sqlite:///./insurance_demo.db"


def _decimal_env(name: str, default: str) -> Decimal:
    raw = os.environ.get(name)
    return Decimal(raw) if raw else Decimal(default)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class InsuranceSettings:
    """Every knob that can change an adjudication outcome."""

    db_url: str = DEFAULT_DB_URL
    currency: str = "USD"
    #: A contestability (incontestability) period: the window in which the
    #: insurer may rescind for misrepresentation. Two years is the industry
    #: norm and the value the sample policy carries. The *policy* stores its own
    #: value; this is the fallback for rows that omit one.
    default_contestability_days: int = 730
    #: Claims at or below this net payout are eligible for straight-through
    #: processing. Above it a human must approve even when every rule passes.
    auto_approval_limit: Decimal = field(default_factory=lambda: Decimal("500000"))
    #: Payments above this amount need a named human approver at the gateway,
    #: independently of the claim decision.
    payment_human_approval_limit: Decimal = field(
        default_factory=lambda: Decimal("100000")
    )
    #: Minimum number of distinct verified evidence types for a death claim.
    required_evidence_types: tuple[str, ...] = (
        "DEATH_CERTIFICATE",
        "CLAIM_FORM",
        "IDENTITY_PROOF",
        "BENEFICIARY_PROOF",
    )
    #: Actor recorded when the engine decides without a human.
    system_actor: str = "system:decision-engine"

    @classmethod
    def from_env(cls) -> "InsuranceSettings":
        return cls(
            db_url=os.environ.get("INSURANCE_DB_URL", DEFAULT_DB_URL),
            currency=os.environ.get("INSURANCE_CURRENCY", "USD"),
            default_contestability_days=_int_env("INSURANCE_CONTESTABILITY_DAYS", 730),
            auto_approval_limit=_decimal_env("INSURANCE_AUTO_APPROVAL_LIMIT", "500000"),
            payment_human_approval_limit=_decimal_env(
                "INSURANCE_PAYMENT_HUMAN_APPROVAL_LIMIT", "100000"
            ),
            system_actor=os.environ.get(
                "INSURANCE_SYSTEM_ACTOR", "system:decision-engine"
            ),
        )


def get_settings() -> InsuranceSettings:
    """Read settings for the current process.

    Not cached: tests flip ``INSURANCE_*`` between cases, and a cached object
    would make a threshold test pass or fail depending on ordering.
    """
    return InsuranceSettings.from_env()
