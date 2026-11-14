# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests: the full scenario over HTTP.

Each test gets a fresh database and a TestClient whose lifespan creates the
schema and catalog, so the flow is exercised exactly as a caller would drive it:
submit, decide, read the graph, pay, verify, audit.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from auto_ontology.insurance.api.app import create_insurance_app
from auto_ontology.insurance.seed import SCENARIO_A_CLAIM_ID, SCENARIO_B_CLAIM_ID


@pytest.fixture()
def client(fresh_db: None) -> Iterator[TestClient]:
    with TestClient(create_insurance_app()) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/api/insurance/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["currency"] == "USD"
    assert "SIMULATED PAYMENT" in body["payment_simulation"]


def test_claims_console_is_served(client: TestClient) -> None:
    """The standalone app serves the console, and it is the real page."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Decision Ontology" in response.text
    assert 'id="rail"' in response.text
    assert client.get("/ui").status_code == 200


def test_data_catalog_lists_every_table(client: TestClient) -> None:
    body = client.get("/api/insurance/data").json()
    assert body["table_count"] == 23
    assert body["database"] == "insurance"
    names = {table["name"] for table in body["tables"]}
    assert {"insurance_claim", "insurance_decision_result"} <= names
    assert body["layers"]["Audit"] == ["insurance_audit_event"]


def test_table_detail_has_columns_and_sample(client: TestClient) -> None:
    body = client.get("/api/insurance/data/insurance_claim").json()
    assert body["row_count"] >= 2
    assert {"claim_number", "policy_id", "status"} <= {
        column["name"] for column in body["columns"]
    }
    assert body["sample"]["columns"]
    assert client.get("/api/insurance/data/not_a_table").status_code == 404


def test_sql_endpoint_is_read_only(client: TestClient) -> None:
    refused = client.post(
        "/api/insurance/sql", json={"sql": "DELETE FROM insurance_claim"}
    )
    assert refused.status_code == 400
    allowed = client.post(
        "/api/insurance/sql",
        json={"sql": "SELECT count(*) AS n FROM insurance_claim"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["rows"][0][0] >= 2


def test_ask_answers_from_the_database(client: TestClient) -> None:
    client.post("/api/insurance/claims/CLM-2001/decision", json={})
    body = client.post(
        "/api/insurance/ask", json={"question": "Why was CLM-2001 approved?"}
    ).json()
    assert body["intent"] == "claim"
    assert "AUTO_APPROVED" in body["answer"]
    assert "rules_applied" in (body["sql"] or "")


def test_analytics_counts(client: TestClient) -> None:
    client.post("/api/insurance/claims/CLM-2001/decision", json={})
    body = client.get("/api/insurance/analytics").json()
    assert body["claims"]["total"] == 2
    assert body["rules"]["catalog"] == 15
    assert body["rules"]["outcomes"] == 15
    assert body["rules"]["failed"] == 0
    assert body["decisions"]["by_status"][0]["status"] == "AUTO_APPROVED"


def test_ontology_catalog(client: TestClient) -> None:
    response = client.get("/api/insurance/ontology")
    assert response.status_code == 200
    body = response.json()
    assert body["catalog"]["namespace"] == "lio:"
    assert len(body["catalog"]["classes"]) >= 25
    assert body["materialized"]["counts"]["classes"] == len(body["catalog"]["classes"])
    assert any(prop["name"] == "coveredBy" for prop in body["catalog"]["properties"])


def test_semantic_and_rule_catalogs(client: TestClient) -> None:
    definitions = client.get("/api/insurance/semantic/definitions").json()
    names = {item["name"] for item in definitions["definitions"]}
    assert "policy_active_days" in names
    assert "net_death_benefit" in names

    rules = client.get("/api/insurance/rules").json()["rules"]
    assert len(rules) == 15
    assert rules[0]["id"] == "R01_POLICY_IN_FORCE"


def test_claim_list_and_detail(client: TestClient) -> None:
    listing = client.get("/api/insurance/claims").json()
    assert listing["count"] == 2

    detail = client.get(f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["claim"]["claim_number"] == "CLM-2026-0001"
    assert body["beneficiaries"][0]["relationship"] == "SPOUSE"
    assert len(body["evidence"]) == 4
    assert body["decision"] is None


def test_end_to_end_auto_approval_and_payment(client: TestClient) -> None:
    decision_response = client.post(
        f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/decision", json={}
    )
    assert decision_response.status_code == 200
    decision = decision_response.json()
    assert decision["approval_status"] == "AUTO_APPROVED"
    assert decision["net_payout_amount"] == "250000.00"
    assert decision["audit_reference"].startswith("AUD-")
    assert decision["knowledge_graph_evidence"]["evidence_chain_text"]
    assert len(decision["decision_rules_applied"]) == 15

    stored = client.get(f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/decision").json()
    assert stored["decision_id"] == decision["decision_id"]
    assert stored["approval_status"] == "AUTO_APPROVED"

    graph = client.get(f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/graph").json()
    assert graph["node_count"] > 10
    assert graph["evidence_chain"]["length"] == 2
    assert graph["ontology_violations"] == []

    refused = client.post(
        f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/payments",
        json={"idempotency_key": "api-claim-1-payout", "approver_id": None},
    )
    assert refused.status_code == 403

    payment_response = client.post(
        f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/payments",
        json={
            "idempotency_key": "api-claim-1-payout",
            "approver_id": "officer:j.mensah",
        },
    )
    assert payment_response.status_code == 201
    payment = payment_response.json()
    assert payment["status"] == "SETTLED"
    assert payment["amount"] == "250000.00"
    assert payment["provider_reference"].startswith("SIM-ACH-")
    assert payment["requires_human_approval"] is True

    replay = client.post(
        f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/payments",
        json={
            "idempotency_key": "api-claim-1-payout",
            "approver_id": "officer:j.mensah",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["payment_id"] == payment["payment_id"]
    assert replay.json()["idempotent_replay"] is True

    verification = client.post(
        f"/api/insurance/payments/{payment['payment_id']}/verify"
    )
    assert verification.status_code == 200
    assert verification.json()["verification_status"] == "VERIFIED"

    claim = client.get(f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}").json()
    assert claim["claim"]["status"] == "PAID"
    assert len(claim["payments"]) == 1


def test_manual_review_blocks_payment_over_http(client: TestClient) -> None:
    decision = client.post(
        f"/api/insurance/claims/{SCENARIO_B_CLAIM_ID}/decision", json={}
    ).json()
    assert decision["approval_status"] == "MANUAL_REVIEW"
    assert decision["net_payout_amount"] == "0.00"
    failed = [
        rule["rule_id"]
        for rule in decision["decision_rules_applied"]
        if rule["status"] == "FAIL"
    ]
    assert failed == ["R10_NO_LEGAL_RESTRICTIONS"]

    payment = client.post(
        f"/api/insurance/claims/{SCENARIO_B_CLAIM_ID}/payments",
        json={
            "idempotency_key": "api-claim-2-payout",
            "approver_id": "officer:j.mensah",
        },
    )
    assert payment.status_code == 409


def test_submit_claim_and_validation_errors(client: TestClient) -> None:
    response = client.post(
        "/api/insurance/claims",
        json={
            "policy_id": "POL-1001",
            "claimant_party_id": "PRT-1002",
            "date_of_death": "2026-06-14T00:00:00",
            "cause_code": "I21.9",
            "death_certificate_id": "DC-9001",
            "evidence": [
                {"evidence_type": "CLAIM_FORM", "status": "PENDING"},
            ],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["claim"]["status"] == "SUBMITTED"
    assert body["claim"]["cause_of_death"]["class_iri"] == "lio:CardiovascularDeath"
    assert len(body["evidence"]) == 1

    invalid = client.post(
        "/api/insurance/claims",
        json={
            "policy_id": "POL-1001",
            "claimant_party_id": "PRT-1002",
            "date_of_death": "2026-06-14T00:00:00",
            "cause_code": "Z99.999",
        },
    )
    assert invalid.status_code == 422


def test_missing_resources_return_404(client: TestClient) -> None:
    assert client.get("/api/insurance/claims/CLM-NOPE").status_code == 404
    assert client.get("/api/insurance/claims/CLM-NOPE/decision").status_code == 404
    assert client.get("/api/insurance/payments/PAY-NOPE").status_code == 404


def test_audit_endpoint_verifies_the_chain(client: TestClient) -> None:
    client.post(f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/decision", json={})
    client.post(
        f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/payments",
        json={
            "idempotency_key": "api-claim-1-audit",
            "approver_id": "officer:j.mensah",
        },
    )
    trail = client.get(f"/api/insurance/claims/{SCENARIO_A_CLAIM_ID}/audit").json()
    assert trail["intact"] is True
    assert trail["events_checked"] >= 3
    actions = [event["action"] for event in trail["events"]]
    assert "CLAIM_DECISION_AUTO_APPROVED" in actions
    assert "PAYMENT_SETTLED" in actions


def test_policy_semantics_endpoint(client: TestClient) -> None:
    response = client.get("/api/insurance/policies/POL-1001/semantics")
    assert response.status_code == 200
    values = {item["name"]: item for item in response.json()["values"]}
    assert values["policy_active_days"]["value"] == 1825
    assert values["net_death_benefit"]["display_value"] == "$250,000.00"
