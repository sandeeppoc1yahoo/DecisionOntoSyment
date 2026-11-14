# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Life insurance claims decision domain, built on Auto Ontology.

Auto-Ontology's own stack answers "what does this enterprise data mean, and how
do I query it reliably": it compiles a *catalog* of tables and columns into a
semantic layer of Terms, SQL attributes and join relationships, then generates
SQL against it. That is a metadata graph over source databases.

A claims decision needs a second, different graph: the *instance* graph of one
insured person, one policy, one death certificate and one claim, traversed to
answer "is this payout owed, and why". This package adds that domain to the same
repository, in four deliberately separate layers:

``schema.py``
    The relational system of record: policies, parties, claims, evidence,
    loans, exclusions, decisions and payments. Facts live here and only here.
``ontology/``
    T-Box: what the business concepts *are*, how they relate, and which
    inference (a heart attack is a natural death) is valid.
``semantic/``
    Certified business definitions and their calculations: active days, net
    death benefit, allocation totals. One definition, one implementation.
``kg/``
    A-Box: the claim's actual entities and relationships, materialised as nodes
    and edges and traversed for evidence.
``decision/``
    Deterministic adjudication over the three layers above, with explainable
    rule outcomes and an audit reference.
``payments/``
    A simulated direct-deposit gateway with idempotency, verification and human
    approval controls. No real money moves.

The layering is the point. Calculations are not re-implemented in rules, rules
do not read raw columns, and the graph never invents a relationship the
ontology does not declare. See ``docs/insurance/README.md`` for the architecture
and the learning notes.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: Version of the decision engine, persisted on every decision result so an
#: audit can reproduce which rule set produced a payout.
__version__ = "1.0.0"
