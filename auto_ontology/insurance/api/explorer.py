# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data catalog, read-only SQL and question answering for the claims console.

The NVIDIA Auto-Ontology UI is a semantic-layer browser: a database tree, term
definitions, exploration and a chat that answers from the catalog. The claims
domain has the same three layers - 23 tables, 31 ontology classes and 14
certified semantic definitions - so this module exposes them in the shapes that
UI needs.

Question answering is deterministic and local. Every intent resolves to a SQL
statement or a catalog lookup and returns the statement alongside the answer, so
a reader can check the number rather than trust a model. A deployment that wires
in an LLM should keep this path as the verifiable one.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from auto_ontology.insurance import service
from auto_ontology.insurance.db import connection_scope
from auto_ontology.insurance.decision.store import load_decision_rules
from auto_ontology.insurance.ontology.store import load_ontology
from auto_ontology.insurance.schema import INSURANCE_METADATA

router = APIRouter()


def _json_list(value: Any) -> list[Any]:
    """Decode a JSON column read through raw SQL.

    A typed ``select()`` hands back the parsed list, but ``text()`` on SQLite
    returns the stored JSON string. Normalise both so readers of this module
    never have to know which one they got.
    """
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []
    return list(value or [])


#: Every table, grouped the way the domain is explained in the docs.
LAYERS: dict[str, tuple[str, ...]] = {
    "Parties & roles": (
        "insurance_party",
        "insurance_policyholder",
        "insurance_insured_person",
        "insurance_beneficiary",
        "insurance_policy_beneficiary",
    ),
    "Policy": (
        "insurance_policy",
        "insurance_policy_loan",
        "insurance_policy_exclusion",
        "insurance_legal_restriction",
    ),
    "Claim": (
        "insurance_claim",
        "insurance_claim_evidence",
        "insurance_cause_of_death",
        "insurance_death_certificate",
    ),
    "Catalog": (
        "insurance_semantic_definition",
        "insurance_ontology_entity",
        "insurance_ontology_relationship",
        "insurance_decision_rule",
    ),
    "Decision": (
        "insurance_decision_result",
        "insurance_claim_approval_history",
        "insurance_kg_node",
        "insurance_kg_edge",
    ),
    "Payments": ("insurance_payment_transaction",),
    "Audit": ("insurance_audit_event",),
}

DESCRIPTIONS: dict[str, str] = {
    "insurance_party": "Any person or organisation the insurer knows about.",
    "insurance_policyholder": "The party that owns the contract.",
    "insurance_insured_person": "The life covered by the policy.",
    "insurance_beneficiary": "A person designated to receive the benefit.",
    "insurance_policy_beneficiary": "Designation: who receives what share of the benefit.",
    "insurance_policy": "Life policy terms: face amount, status and contestability window.",
    "insurance_policy_loan": "Outstanding loan against the policy, deducted from the payout.",
    "insurance_policy_exclusion": "Contract exclusions that can defeat cover.",
    "insurance_legal_restriction": "Court orders and similar blocks on paying a claim.",
    "insurance_claim": "A death claim submitted against a policy.",
    "insurance_claim_evidence": "Documents supporting the claim and their verification state.",
    "insurance_cause_of_death": "ICD-10 cause map to ontology classes and categories.",
    "insurance_death_certificate": "The certificate the cause of death was read from.",
    "insurance_semantic_definition": "Certified semantic definitions with formulas and versions.",
    "insurance_ontology_entity": "Materialised T-Box classes.",
    "insurance_ontology_relationship": "Materialised object properties with domain and range.",
    "insurance_decision_rule": "The 15 deterministic adjudication rules.",
    "insurance_decision_result": "A decision, its rule outcomes, semantic values and graph evidence.",
    "insurance_claim_approval_history": "Status transitions with actor and reason.",
    "insurance_kg_node": "Knowledge-graph nodes built for the claim.",
    "insurance_kg_edge": "Knowledge-graph edges with their ontology property.",
    "insurance_payment_transaction": "Simulated payment: authorisation, settlement and verification.",
    "insurance_audit_event": "Hash-chained audit events.",
}


def _layer_for(table: str) -> str:
    for layer, names in LAYERS.items():
        if table in names:
            return layer
    return "Other"


def _row_count(connection, table: str) -> int:
    return int(
        connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0
    )


@router.get("/data")
def data_catalog() -> dict[str, Any]:
    """Database tree: every claims table with its layer, column count and rows."""
    with connection_scope() as connection:
        tables = []
        for name in sorted(INSURANCE_METADATA.tables):
            table = INSURANCE_METADATA.tables[name]
            tables.append(
                {
                    "name": name,
                    "type": "table",
                    "layer": _layer_for(name),
                    "description": DESCRIPTIONS.get(name, ""),
                    "columns": len(table.columns),
                    "rows": _row_count(connection, name),
                }
            )
    layers: dict[str, list[str]] = {}
    for entry in tables:
        layers.setdefault(entry["layer"], []).append(entry["name"])
    return {
        "database": "insurance",
        "schema": "public",
        "table_count": len(tables),
        "row_total": sum(entry["rows"] for entry in tables),
        "layers": layers,
        "tables": tables,
    }


@router.get("/data/{table_name}")
def table_detail(table_name: str) -> dict[str, Any]:
    """Columns, keys, relationships and a sample of one claims table."""
    if table_name not in INSURANCE_METADATA.tables:
        raise HTTPException(status_code=404, detail=f"Unknown table {table_name!r}")
    table = INSURANCE_METADATA.tables[table_name]
    columns = []
    relationships = []
    for column in table.columns:
        foreign = next(iter(column.foreign_keys), None)
        columns.append(
            {
                "name": column.name,
                "type": str(column.type),
                "nullable": bool(column.nullable),
                "primary_key": bool(column.primary_key),
                "foreign_key": foreign.target_fullname if foreign else None,
            }
        )
        if foreign:
            relationships.append(
                {"column": column.name, "references": foreign.target_fullname}
            )
    with connection_scope() as connection:
        rows = connection.execute(
            text(f'SELECT * FROM "{table_name}" LIMIT 10')
        ).fetchall()
        keys = list(
            connection.execute(text(f'SELECT * FROM "{table_name}" LIMIT 0')).keys()
        )
        total = _row_count(connection, table_name)
    return {
        "name": table_name,
        "type": "table",
        "layer": _layer_for(table_name),
        "description": DESCRIPTIONS.get(table_name, ""),
        "row_count": total,
        "column_count": len(columns),
        "columns": columns,
        "relationships": relationships,
        "sample": {
            "columns": keys,
            "rows": [[service.to_jsonable(value) for value in row] for row in rows],
        },
    }


class SqlRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=8000)
    limit: int = Field(default=200, ge=1, le=1000)


_BANNED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|detach"
    r"|pragma|vacuum|copy|call|merge|reindex|analyze)\b",
    re.IGNORECASE,
)


def _read_only(sql: str) -> str:
    """One SELECT (or WITH ... SELECT), nothing else."""
    cleaned = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL).strip()
    cleaned = cleaned.rstrip(";").strip()
    if not re.match(r"(?is)^(select|with)\b", cleaned):
        raise HTTPException(status_code=400, detail="Only SELECT queries are allowed.")
    if ";" in cleaned:
        raise HTTPException(status_code=400, detail="One statement at a time.")
    if _BANNED.search(cleaned):
        raise HTTPException(
            status_code=400,
            detail="Only read-only queries: DDL/DML keywords are refused.",
        )
    return cleaned


@router.post("/sql")
def run_sql(body: SqlRequest) -> dict[str, Any]:
    """Execute a read-only query for the Exploration page."""
    statement = _read_only(body.sql)
    started = time.perf_counter()
    try:
        with connection_scope() as connection:
            result = connection.execute(text(statement))
            columns = list(result.keys())
            rows = result.fetchmany(body.limit + 1)
    except Exception as exc:  # surfaced to the exploration UI verbatim
        raise HTTPException(status_code=400, detail=str(exc).splitlines()[0]) from exc
    truncated = len(rows) > body.limit
    rows = rows[: body.limit]
    return {
        "sql": statement,
        "columns": columns,
        "rows": [[service.to_jsonable(value) for value in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@router.get("/analytics")
def analytics() -> dict[str, Any]:
    """Counts a reader would otherwise write five queries to get."""
    with connection_scope() as connection:
        claim_status = connection.execute(
            text(
                "SELECT status, count(*) FROM insurance_claim GROUP BY status ORDER BY status"
            )
        ).all()
        decision_status = connection.execute(
            text(
                "SELECT status, count(*), sum(net_payout_amount) "
                "FROM insurance_decision_result WHERE is_current = :current "
                "GROUP BY status ORDER BY status"
            ),
            {"current": True},
        ).all()
        payments = connection.execute(
            text(
                "SELECT status, count(*), coalesce(sum(amount), 0) "
                "FROM insurance_payment_transaction GROUP BY status ORDER BY status"
            )
        ).all()
        audit_events = connection.execute(
            text("SELECT count(*) FROM insurance_audit_event")
        ).scalar()
        claims = connection.execute(
            text("SELECT count(*) FROM insurance_claim")
        ).scalar()
        rules = load_decision_rules(connection)
        decisions = connection.execute(
            text("SELECT rules_applied, status FROM insurance_decision_result")
        ).all()

    total_rules = 0
    failed_rules = 0
    for applied, _status in decisions:
        for entry in _json_list(applied):
            total_rules += 1
            if isinstance(entry, dict) and entry.get("status") != "PASS":
                failed_rules += 1

    return {
        "claims": {
            "total": int(claims or 0),
            "by_status": [
                {"status": status, "count": count} for status, count in claim_status
            ],
        },
        "decisions": {
            "total": sum(count for _, count, _ in decision_status),
            "by_status": [
                {
                    "status": status,
                    "count": count,
                    "payout": f"{int(payout or 0) / 100:.2f}",
                }
                for status, count, payout in decision_status
            ],
        },
        "payments": {
            "by_status": [
                {
                    "status": status,
                    "count": count,
                    "amount": f"{int(amount or 0) / 100:.2f}",
                }
                for status, count, amount in payments
            ],
        },
        "rules": {
            "catalog": len(rules),
            "outcomes": total_rules,
            "failed": failed_rules,
            "pass_rate": round(100 * (total_rules - failed_rules) / total_rules, 1)
            if total_rules
            else None,
        },
        "audit_events": int(audit_events or 0),
    }


# --------------------------------------------------------------------- chat
class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)


def _table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": rows}


def _query(
    connection, sql: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = connection.execute(text(sql), params or {})
    columns = list(result.keys())
    rows = [[service.to_jsonable(value) for value in row] for row in result.fetchall()]
    return _table(columns, rows)


def _claim_summary(connection, claim_id: str) -> dict[str, Any]:
    detail = service.claim_view(connection, claim_id)
    claim = detail["claim"]
    policy = detail["policy"]
    decision = detail.get("decision") or {}
    return {
        "answer": (
            f"{claim_id} is {claim['status']} under policy {policy['policy_number']} "
            f"({policy['product']}). Cause {claim['cause_code']} "
            f"({claim['cause_of_death']['description']}); face amount "
            f"${policy['face_amount']}."
            + (
                f" Decision: {decision.get('approval_status')} for "
                f"${decision.get('net_payout_amount')}."
                if decision
                else " No decision has been recorded yet."
            )
        ),
        "table": _table(
            ["claim", "policy", "insured", "status", "decision", "payout"],
            [
                [
                    claim_id,
                    policy["policy_number"],
                    detail["insured"]["name"],
                    claim["status"],
                    decision.get("approval_status", "—"),
                    decision.get("net_payout_amount", "—"),
                ]
            ],
        ),
        "sql": (
            "SELECT c.claim_id, c.status, p.policy_number, d.status AS decision_status, "
            "d.net_payout_amount FROM insurance_claim c "
            "JOIN insurance_policy p ON p.policy_id = c.policy_id "
            "LEFT JOIN insurance_decision_result d ON d.claim_id = c.claim_id "
            "AND d.is_current = true WHERE c.claim_id = :claim_id"
        ),
        "sources": ["insurance_claim", "insurance_policy", "insurance_decision_result"],
    }


def _claim_intent(connection, claim_id: str, question: str) -> dict[str, Any]:
    lowered = question.lower()
    if "why" in lowered or "explain" in lowered or "decision" in lowered:
        detail = service.claim_view(connection, claim_id)
        decision = detail.get("decision")
        if not decision:
            return {
                "answer": f"{claim_id} has no decision yet. Run one from Analysis.",
                "table": None,
                "sql": None,
                "sources": ["insurance_claim"],
            }
        failed = [
            f"{rule['rule_id']} — {rule['message']}"
            for rule in decision["decision_rules_applied"]
            if rule["status"] != "PASS"
        ]
        answer = decision["decision_explanation"]
        if failed:
            answer += " Failed rules: " + "; ".join(failed)
        return {
            "answer": answer,
            "table": _table(
                ["rule", "result", "why"],
                [
                    [rule["rule_id"], rule["status"], rule["message"]]
                    for rule in decision["decision_rules_applied"]
                ],
            ),
            "sql": (
                "SELECT rules_applied FROM insurance_decision_result "
                "WHERE claim_id = :claim_id AND is_current = true"
            ),
            "sources": ["insurance_decision_result", "insurance_decision_rule"],
        }
    if "payment" in lowered or "paid" in lowered:
        detail = service.claim_view(connection, claim_id)
        payments = detail.get("payments") or []
        return {
            "answer": (
                f"{len(payments)} payment(s) for {claim_id}: "
                + ", ".join(
                    f"{p['payment_id']} {p['status']} ${p['amount']}" for p in payments
                )
                if payments
                else f"No payment has been made for {claim_id}."
            ),
            "table": _table(
                ["payment", "status", "amount", "provider", "idempotency key"],
                [
                    [
                        p["payment_id"],
                        p["status"],
                        p["amount"],
                        p["provider_reference"],
                        p["idempotency_key"],
                    ]
                    for p in payments
                ],
            ),
            "sql": "SELECT * FROM insurance_payment_transaction WHERE claim_id = :claim_id",
            "sources": ["insurance_payment_transaction"],
        }
    if "audit" in lowered or "chain" in lowered:
        trail = service.audit_trail(connection, claim_id)
        return {
            "answer": (
                f"The audit chain for {claim_id} has {trail['events_checked']} events and is "
                f"{'intact' if trail['intact'] else 'broken at ' + str(trail['broken_at'])}."
            ),
            "table": _table(
                ["event", "actor", "action", "occurred_at"],
                [
                    [e["event_id"], e["actor"], e["action"], e["occurred_at"]]
                    for e in trail["events"]
                ],
            ),
            "sql": "SELECT * FROM insurance_audit_event WHERE entity_id = :claim_id ORDER BY occurred_at",
            "sources": ["insurance_audit_event"],
        }
    if "beneficiar" in lowered:
        detail = service.claim_view(connection, claim_id)
        people = detail["beneficiaries"]
        return {
            "answer": ", ".join(
                f"{p['name']} ({p['designation_type'].lower()}, {p['allocation_percent']}%, "
                f"{p['verification_status'].lower()})"
                for p in people
            ),
            "table": _table(
                ["id", "name", "relationship", "designation", "allocation", "verified"],
                [
                    [
                        p["beneficiary_id"],
                        p["name"],
                        p["relationship"],
                        p["designation_type"],
                        p["allocation_percent"],
                        p["verification_status"],
                    ]
                    for p in people
                ],
            ),
            "sql": "SELECT * FROM insurance_beneficiary WHERE beneficiary_id IN (...)",
            "sources": ["insurance_beneficiary"],
        }
    if "evidence" in lowered or "document" in lowered:
        detail = service.claim_view(connection, claim_id)
        rows = detail["evidence"]
        return {
            "answer": f"{len(rows)} evidence item(s), all {rows[0]['status'].lower() if rows else 'n/a'}.",
            "table": _table(
                ["type", "document", "status", "verified by"],
                [
                    [
                        r["evidence_type"],
                        r["document_ref"],
                        r["status"],
                        r["verified_by"],
                    ]
                    for r in rows
                ],
            ),
            "sql": "SELECT * FROM insurance_claim_evidence WHERE claim_id = :claim_id",
            "sources": ["insurance_claim_evidence"],
        }
    return _claim_summary(connection, claim_id)


def _ontology_intent(connection, question: str) -> dict[str, Any]:
    catalog = load_ontology(connection)["catalog"]
    lowered = question.lower()
    for constraint in catalog["constraints"]:
        if constraint["code"].lower() in lowered:
            return {
                "answer": (
                    f"{constraint['code']}: {constraint['statement']} "
                    f"If violated: {constraint['consequence']}"
                ),
                "table": _table(
                    ["code", "statement", "consequence"],
                    [
                        [
                            constraint["code"],
                            constraint["statement"],
                            constraint["consequence"],
                        ]
                    ],
                ),
                "sql": "SELECT * FROM insurance_ontology_entity",
                "sources": ["insurance_ontology_entity"],
            }
    for item in catalog["classes"]:
        if re.search(rf"\b{re.escape(item['name'].lower())}\b", lowered):
            return {
                "answer": f"{item['iri']}: {item['description']}",
                "table": _table(
                    ["class", "parents", "description"],
                    [
                        [
                            item["name"],
                            ", ".join(item["parents"]) or "root",
                            item["description"],
                        ]
                    ],
                ),
                "sql": "SELECT * FROM insurance_ontology_entity",
                "sources": ["insurance_ontology_entity"],
            }
    return {
        "answer": (
            f"The ontology has {len(catalog['classes'])} classes, "
            f"{len(catalog['properties'])} properties and {len(catalog['constraints'])} constraints, "
            "with an ICD-10 cause map and product exclusions."
        ),
        "table": _table(
            ["constraint", "statement"],
            [[c["code"], c["statement"]] for c in catalog["constraints"]],
        ),
        "sql": "SELECT * FROM insurance_ontology_entity",
        "sources": ["insurance_ontology_entity", "insurance_ontology_relationship"],
    }


def _semantic_intent(connection, question: str) -> dict[str, Any]:
    policy_match = re.search(r"\bPOL-\d+\b", question, re.IGNORECASE)
    policy_id = policy_match.group(0) if policy_match else None
    if policy_id is None:
        row = connection.execute(
            text("SELECT claim_id, policy_id FROM insurance_claim LIMIT 1")
        ).first()
        policy_id = row[1] if row else None
    if policy_id is None:
        raise HTTPException(status_code=404, detail="No policy in the database.")
    values = service.policy_semantics(connection, policy_id)["values"]
    for value in values:
        if value["name"].lower() in question.lower():
            return {
                "answer": (
                    f"{value['display_name']} = {value['display_value']} "
                    f"({value['definition_kind']}, v{value['definition_version']}). "
                    f"Formula: {value['formula']}"
                ),
                "table": _table(
                    ["definition", "value", "formula", "inputs"],
                    [
                        [
                            value["name"],
                            value["display_value"],
                            value["formula"],
                            value["inputs"],
                        ]
                    ],
                ),
                "sql": "SELECT * FROM insurance_semantic_definition WHERE name = :name",
                "sources": ["insurance_semantic_definition"],
            }
    return {
        "answer": (
            f"{len(values)} semantic definitions evaluated for {policy_id}. "
            "Ask for one by name, e.g. policy_active_days or net_death_benefit."
        ),
        "table": _table(
            ["definition", "value", "formula"],
            [[v["name"], v["display_value"], v["formula"]] for v in values],
        ),
        "sql": "SELECT * FROM insurance_semantic_definition",
        "sources": ["insurance_semantic_definition"],
    }


@router.post("/ask")
def ask(body: AskRequest) -> dict[str, Any]:
    """Answer a question from the data, ontology or semantic layer.

    Deterministic: each intent maps to a lookup or a SQL statement, and the
    statement is returned with the answer.
    """
    question = body.question.strip()
    lowered = question.lower()
    with connection_scope() as connection:
        try:
            if re.match(r"(?is)^(select|with)\b", question):
                guarded = _read_only(question)
                executed = connection.execute(text(guarded))
                columns = list(executed.keys())
                result = executed.fetchmany(50)
                return {
                    "question": question,
                    "intent": "sql",
                    "answer": f"Query returned {len(result)} row(s).",
                    "sql": guarded,
                    "table": {
                        "columns": columns,
                        "rows": [
                            [service.to_jsonable(value) for value in row]
                            for row in result
                        ],
                    },
                    "sources": [],
                    "suggestions": [],
                }

            claim_match = re.search(r"\bCLM-\d+\b", question, re.IGNORECASE)
            if claim_match:
                result = _claim_intent(
                    connection, claim_match.group(0).upper(), question
                )
                intent = "claim"
            elif "rules" in lowered:
                rules = load_decision_rules(connection)
                failed = connection.execute(
                    text(
                        "SELECT claim_id, rules_applied FROM insurance_decision_result "
                        "WHERE is_current = true"
                    )
                ).all()
                rows = []
                for claim_id, applied in failed:
                    for rule in _json_list(applied):
                        if isinstance(rule, dict) and rule["status"] != "PASS":
                            rows.append(
                                [
                                    claim_id,
                                    rule["rule_id"],
                                    rule["status"],
                                    rule["message"],
                                ]
                            )
                result = {
                    "answer": (
                        f"{len(rules)} decision rules are loaded. "
                        f"{len(rows)} failed outcome(s) across current decisions."
                        if rows
                        else f"{len(rules)} decision rules are loaded; no current decision has a failure."
                    ),
                    "table": _table(
                        ["claim", "rule", "result", "message"],
                        rows,
                    )
                    if rows
                    else _table(
                        ["rule", "category", "severity", "action"],
                        [
                            [r["id"], r["category"], r["severity"], r["on_fail_action"]]
                            for r in rules
                        ],
                    ),
                    "sql": "SELECT rules_applied FROM insurance_decision_result WHERE is_current = true",
                    "sources": ["insurance_decision_rule", "insurance_decision_result"],
                }
                intent = "rules"
            elif "ontology" in lowered or "constraint" in lowered or "class" in lowered:
                result = _ontology_intent(connection, question)
                intent = "ontology"
            elif any(
                token in lowered
                for token in (
                    "semantic",
                    "definition",
                    "active days",
                    "net_death_benefit",
                    "payout",
                )
            ):
                result = _semantic_intent(connection, question)
                intent = "semantic"
            elif "payment" in lowered or "paid" in lowered:
                rows = connection.execute(
                    text(
                        "SELECT claim_id, payment_id, status, amount, provider_reference "
                        "FROM insurance_payment_transaction ORDER BY created_at"
                    )
                ).all()
                total = sum(int(row[3]) for row in rows if row[2] == "SETTLED")
                result = {
                    "answer": (
                        f"{len(rows)} payment(s) recorded; ${service.to_jsonable(total)} settled "
                        "(simulated)."
                    ),
                    "table": _table(
                        ["claim", "payment", "status", "amount", "provider"],
                        [
                            [r[0], r[1], r[2], str(service.to_jsonable(r[3])), r[4]]
                            for r in rows
                        ],
                    ),
                    "sql": "SELECT * FROM insurance_payment_transaction ORDER BY created_at",
                    "sources": ["insurance_payment_transaction"],
                }
                intent = "payments"
            elif "claim" in lowered or "status" in lowered:
                listing = service.list_claims(connection, limit=50)
                result = {
                    "answer": f"{len(listing)} claim(s): "
                    + ", ".join(
                        f"{c['claim_id']} {c['status']} ({c['decision_status'] or 'no decision'})"
                        for c in listing
                    ),
                    "table": _table(
                        ["claim", "policy", "status", "decision", "payout"],
                        [
                            [
                                c["claim_id"],
                                c["policy_number"],
                                c["status"],
                                c["decision_status"],
                                c["net_payout_amount"],
                            ]
                            for c in listing
                        ],
                    ),
                    "sql": "SELECT * FROM insurance_claim",
                    "sources": ["insurance_claim", "insurance_decision_result"],
                }
                intent = "claims"
            else:
                catalog = load_ontology(connection)["catalog"]
                result = {
                    "answer": (
                        "I answer from the insurance database, its ontology and its semantic layer. "
                        f"There are {len(INSURANCE_METADATA.tables)} tables, "
                        f"{len(catalog['classes'])} ontology classes and 14 certified definitions. "
                        "Try asking about a claim, the rules, a semantic definition or a constraint."
                    ),
                    "table": None,
                    "sql": None,
                    "sources": [],
                }
                intent = "overview"
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    result.setdefault("table", None)
    result.setdefault("sql", None)
    result.setdefault("sources", [])
    result["question"] = question
    result["intent"] = intent
    result["suggestions"] = [
        "What is the status of CLM-2001?",
        "Why was CLM-2001 approved?",
        "Show the audit chain for CLM-2001",
        "Which rules failed?",
        "What is net_death_benefit for POL-1001?",
        "List the ontology constraints",
        "Which claims are under manual review?",
    ]
    return result
