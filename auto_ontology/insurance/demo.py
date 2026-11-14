# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end demonstration of the death claim decision.

Run from the repository root::

    uv run python -m auto_ontology.insurance.demo

It drops and reseeds a SQLite database, then walks the same claim through all
five stages - semantic calculations, ontology validation, knowledge-graph
traversal, decision, simulated payment - printing what each layer produced and
why. A second claim shows the engine refusing to auto-approve when a court order
restricts payment.

The JSON artifacts written to ``demo_output/`` are the evidence for the API
response shapes; the terminal output is the narrative.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_ontology.insurance.bootstrap import reset_and_seed
from auto_ontology.insurance.config import get_settings
from auto_ontology.insurance.db import (
    configure_engine,
    connection_scope,
    dispose_engine,
    transaction,
)
from auto_ontology.insurance.decision.context import build_context
from auto_ontology.insurance.decision.engine import decide_claim
from auto_ontology.insurance.payments.gateway import SimulatedPaymentGateway
from auto_ontology.insurance.seed import SCENARIO_A_CLAIM_ID, SCENARIO_B_CLAIM_ID
from auto_ontology.insurance.service import audit_trail, claim_view, to_jsonable
from auto_ontology.insurance.semantic.engine import SemanticEngine

RULE = "=" * 96
THIN = "-" * 96
ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "demo_output"


def _title(text: str) -> None:
    print(f"\n{RULE}\n {text}\n{RULE}")


def _step(number: int, text: str) -> None:
    print(f"\n{THIN}\n STEP {number} - {text}\n{THIN}")


def _money(value: Any) -> str:
    return f"${Decimal(str(value)):,.2f}"


def _show_semantics(values: list[str], claim_id: str) -> None:
    with connection_scope() as connection:
        result = SemanticEngine().evaluate(connection, claim_id)
    for name in values:
        value = result.values[name]
        print(
            f"  {value.display_name:<38} {value.display_value:>28}"
            f"   [{value.definition_kind}, v{value.version}]"
        )
    print(
        f"\n  {len(result.values)} certified definitions evaluated; each value carries "
        "its formula, version and inputs."
    )


def main() -> int:
    db_url = os.environ.get("INSURANCE_DB_URL", "sqlite:///./insurance_demo.db")
    configure_engine(db_url)
    settings = get_settings()

    _title(
        "LIFE INSURANCE CLAIMS DECISION AI - SEMANTIC LAYER + ONTOLOGY + KG + DECISION"
    )
    print(f"  Storage       : {db_url}")
    print(
        "  Engine        : deterministic rules over certified calculations and inference"
    )
    print("  Money         : simulated only - no financial transaction is executed")
    report = reset_and_seed()
    print(
        f"  Bootstrap     : {report['ontology']['entities']} ontology classes, "
        f"{report['ontology']['relationships']} relationships, "
        f"{report['semantic_definitions']} semantic definitions, "
        f"{report['decision_rules']} decision rules"
    )

    with connection_scope() as connection:
        view = claim_view(connection, SCENARIO_A_CLAIM_ID)
    claim = view["claim"]
    policy = view["policy"]
    print(
        f"\n  Claim {claim['claim_number']} | policy {policy['policy_number']} | "
        f"{policy['product']} | {_money(policy['face_amount'])}"
    )
    print(
        f"  Insured died {claim['date_of_death'][:10]} of {claim['cause_code']} "
        f"({claim['cause_of_death']['description']})"
    )
    print(
        "  Beneficiary: "
        + ", ".join(
            f"{b['name']} ({b['relationship']}, {b['designation_type']}, "
            f"{b['allocation_percent']}%)"
            for b in view["beneficiaries"]
        )
    )

    # -- Step 1 ------------------------------------------------------------
    _step(1, "SEMANTIC LAYER - business definitions and calculations")
    _show_semantics(
        [
            "policy_effective_date",
            "policy_expiry_date",
            "policy_active_days",
            "contestability_period_days",
            "past_contestability",
            "policy_face_amount",
            "outstanding_policy_loan_balance",
            "net_death_benefit",
            "total_primary_allocation_percent",
            "payout_amount",
            "claim_eligibility",
        ],
        SCENARIO_A_CLAIM_ID,
    )
    print("  -> The decision engine will read these numbers; it never recomputes them.")

    # -- Step 2 ------------------------------------------------------------
    with transaction() as connection:
        context = build_context(connection, SCENARIO_A_CLAIM_ID, persist=True)
    _step(2, "ONTOLOGY - business meaning, inference and constraints")
    classification = context.classification
    print(f"  Cause {classification.code}: {classification.description}")
    print(f"  Inference   : {classification.inference}")
    print(f"  Category    : {classification.category}")
    print("\n  Constraints evaluated against the claim's facts:")
    for validation in context.ontology:
        print(f"    {validation.code:<28} {validation.status:<9} {validation.evidence}")
    print(
        f"\n  Exclusions on the policy: "
        f"{', '.join(e['exclusion_code'] for e in context.exclusions)}"
    )
    print(
        "  Contestability-only exclusions are inert once the period has passed, so "
        "only cause-class exclusions were considered."
    )

    # -- Step 3 ------------------------------------------------------------
    _step(3, "KNOWLEDGE GRAPH - relationship traversal and evidence")
    graph = context.graph
    print(f"  Nodes {len(graph.nodes)} | Edges {len(graph.edges)}")
    counts = {}
    for node in graph.nodes:
        counts[node.class_name] = counts.get(node.class_name, 0) + 1
    print(
        "  " + ", ".join(f"{name} x{count}" for name, count in sorted(counts.items()))
    )
    print(f"\n  Required chain: {context.evidence_chain_description()}")
    if context.evidence_chain:
        hops = context.evidence_chain.to_dict()["hops"]
        for index, hop in enumerate(hops, start=1):
            print(f"    {index}. {hop['from']} --{hop['relation']}--> {hop['to']}")
    print(f"\n  Ontology domain/range violations: {len(context.graph_violations)}")
    print("  First relationships:")
    for statement in graph.statements(limit=8):
        print(f"    {statement}")

    # -- Step 4 ------------------------------------------------------------
    with transaction() as connection:
        decision = decide_claim(connection, SCENARIO_A_CLAIM_ID)
    _step(4, "DECISION AI - deterministic adjudication")
    print(f"  STATUS: {decision.status}")
    print(f"  Net payout: {_money(decision.net_payout_amount)} {decision.currency}")
    print(f"  Confidence (rules passed / applicable): {decision.confidence_score:.2f}")
    print(f"  Audit reference: {decision.audit_reference}")
    print("\n  Rules applied:")
    for rule in decision.rules_applied:
        print(
            f"    {rule['status']:<4} {rule['rule_id']:<34} "
            f"{rule['category']:<15} {rule['message']}"
        )
    print("\n  Explanation:")
    for line in decision.explanation.splitlines()[:18]:
        print(f"  {line}")

    # -- Step 5 ------------------------------------------------------------
    _step(5, "SIMULATED PAYMENT - authorised direct deposit")
    idempotency_key = f"claim-{SCENARIO_A_CLAIM_ID}-payout-1"
    with transaction() as connection:
        payment = SimulatedPaymentGateway(settings).initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=idempotency_key,
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )
    record = payment.to_dict()
    print(f"  Payment {record['payment_id']} | {record['status']}")
    print(
        f"  Amount  {_money(record['amount'])} {record['currency']} via {record['method']}"
    )
    print(f"  Account {record['account']} (routing {record['routing']})")
    print(f"  Authorisation {record['authorization_reference']}")
    print(f"  Provider ref  {record['provider_reference']}")
    print(
        f"  Human approval required: {record['requires_human_approval']} "
        f"(approved_by={record['approved_by']})"
    )
    print(f"  Verification: {record['verification_status']}")
    print(f"  {record['simulation_notice']}")

    with transaction() as connection:
        replay = SimulatedPaymentGateway(settings).initiate(
            connection,
            claim_id=SCENARIO_A_CLAIM_ID,
            idempotency_key=idempotency_key,
            actor="officer:claims-intake",
            approver_id="officer:j.mensah",
        )
        verification = SimulatedPaymentGateway(settings).verify(
            connection, record["payment_id"]
        )
    print(
        f"\n  Retry with the same idempotency key returned payment "
        f"{replay.id} - idempotent_replay={replay.idempotent_replay}."
    )
    print("  Verification checks:")
    for check in verification["checks"]:
        print(f"    {'PASS' if check['passed'] else 'FAIL'}  {check['check']}")

    # -- Step 6 ------------------------------------------------------------
    _step(6, "AUDIT TRAIL - hash-chained events")
    with connection_scope() as connection:
        trail = audit_trail(connection, SCENARIO_A_CLAIM_ID)
    print(f"  Chain intact: {trail['intact']} | events: {trail['events_checked']}")
    for event in trail["events"]:
        print(
            f"    {event['occurred_at'][:19]}  {event['action']:<26} {event['actor']}"
        )

    # -- Counter-example ---------------------------------------------------
    _step(7, "COUNTER-EXAMPLE - same engine, restricted payment")
    with transaction() as connection:
        manual = decide_claim(connection, SCENARIO_B_CLAIM_ID)
    print(f"  Claim {SCENARIO_B_CLAIM_ID}: {manual.status}")
    print(f"  Net payout authorised: {_money(manual.net_payout_amount)}")
    print(f"  Decision path: {manual.decision_path}")
    for rule in manual.rules_applied:
        if rule["status"] == "FAIL":
            print(f"    FAIL {rule['rule_id']}: {rule['message']}")
    with transaction() as connection:
        try:
            SimulatedPaymentGateway(settings).initiate(
                connection,
                claim_id=SCENARIO_B_CLAIM_ID,
                idempotency_key="claim-CLM-2002-payout-1",
                actor="officer:claims-intake",
                approver_id="officer:j.mensah",
            )
        except Exception as exc:  # noqa: BLE001 - the demo is showing the refusal
            print(f"  Payment attempt refused: {type(exc).__name__}: {exc}")

    # -- Artifacts ---------------------------------------------------------
    ARTIFACT_DIR.mkdir(exist_ok=True)
    (ARTIFACT_DIR / "decision_auto_approved.json").write_text(
        json.dumps(to_jsonable(decision.to_dict()), indent=2) + "\n", encoding="utf-8"
    )
    (ARTIFACT_DIR / "decision_manual_review.json").write_text(
        json.dumps(to_jsonable(manual.to_dict()), indent=2) + "\n", encoding="utf-8"
    )
    (ARTIFACT_DIR / "payment_settled.json").write_text(
        json.dumps(
            to_jsonable(
                {
                    "payment": record,
                    "verification": verification,
                    "audit_chain": trail,
                }
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (ARTIFACT_DIR / "knowledge_graph.json").write_text(
        json.dumps(to_jsonable(graph.to_dict()), indent=2) + "\n", encoding="utf-8"
    )

    _title("RESULT")
    print(
        f"  {decision.status}: {claim['claim_number']} -> "
        f"{_money(decision.net_payout_amount)} to Jane Marie Hayes"
    )
    print(
        f"  Payment {record['payment_id']} {record['status']} and "
        f"{verification['verification_status']}"
    )
    print(
        f"  {manual.status}: {SCENARIO_B_CLAIM_ID} refused payment pending human review"
    )
    print(f"  Artifacts written to {ARTIFACT_DIR}")
    print(
        "\n  Deterministic: calculations, inference, rule outcomes, payout, audit hashes."
        "\n  AI-assisted: ontology/term generation from source schemas (Auto-Ontology's"
        "\n  NIM-backed semantic compilation), used upstream of this scenario.\n"
    )
    dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
