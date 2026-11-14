# Life Insurance Claims Decision AI

An executable extension of **NVIDIA Auto-Ontology** that adjudicates a life
insurance death claim using four cooperating layers:

1. a **semantic layer** of certified business definitions and calculations,
2. a **domain ontology** (T-Box) for classification, relationships and constraints,
3. a **knowledge graph** (A-Box) of the claim's actual people, policy, evidence and obligations,
4. a **deterministic decision engine** that consumes the three above and produces
   an explainable, auditable outcome,
5. a **simulated payment rail** with idempotency, human approval and verification.

The running scenario in this repository approves a $250,000 death claim end to
end and then refuses to pay a second claim because a court order restricts it.

> **No real money moves.** Every payment is simulated: provider references are
> prefixed `SIM-`, account numbers are pseudonyms derived from beneficiary ids,
> and the only side effect is a row in this database.

---

## 1. What was added to the repository, and why

The reference repository is a full-stack *metadata-to-ontology* platform. Its
existing pieces were analysed first; nothing was replaced.

### 1.1 Existing architecture (as found)

| Area | Location | Role |
| --- | --- | --- |
| Catalog ingestion | `auto_ontology/catalog/` | Reads source schemas, profiles columns, stores tables/columns/keys |
| Semantic compilation | `auto_ontology/semantic/` | LLM + deterministic BFS that proposes **Terms**, column attributes, joins and SQL attributes |
| Ontology store | `auto_ontology/dal/` (`schema.py`, `terms.py`, `sql_attributes.py`) | Postgres tables: `term`, `column_attribute`, `sql_attribute`, `catalog_*`, embeddings in pgvector |
| Retrieval / NL-to-SQL | `auto_ontology/retrieval/` | Intent, entity extraction, candidate retrieval, SQL generation, repair |
| APIs | `auto_ontology/server/` | FastAPI on :3001, identity via gateway header, routes under `/api` |
| Ingestion service | `auto_ontology/ingestion_service/` | FastAPI on :3002, runs ingestion + compilation |
| Web / MCP | `frontend/`, `mcp/` | Next.js UI and read-only MCP tools |
| Migrations | `alembic/` | Single Postgres schema, one migration history |

**What the existing semantic layer is.** Its Terms, SQL attributes and
relationships are a semantic model *over source databases*: they answer "what
does this column mean and how do I join it". It is the right layer for
governed text-to-SQL, but it cannot express "net death benefit" as a decision
input, because that number is a fact about a claim event, not a SQL expression
over a source table.

**What the existing graph is.** The term/attribute/relationship graph is a
*metadata graph*. It has no insured person, no death certificate and no policy
loan. A claims decision needs an *instance* graph.

### 1.2 The gap

| Capability | Existed? | What the platform would have needed |
| --- | --- | --- |
| Business terminology for tables | Yes | - |
| Certified cross-domain metric definitions with versions and owners | Partial (SQL attributes) | A claims-domain catalog where a metric is a decision input, not a SQL fragment |
| Class hierarchy and inference ("heart attack is a natural death") | No | T-Box vocabulary + subsumption + exclusion matching |
| Instance-level knowledge graph with path queries | No | A-Box builder, traversal, ontology domain/range validation |
| Deterministic adjudication with per-rule outcomes | No | Rule set, aggregation policy, explanation, audit |
| Payments with idempotency and approval controls | No | Simulated gateway |
| Claims system of record | No | 23 domain tables |

### 1.3 What was implemented

```
auto_ontology/insurance/
  schema.py           23 relational tables (system of record)
  db.py               engine/transaction scope, portable across SQLite and Postgres
  types.py            money as cents, percentage as basis points
  repository.py       one set of reads shared by every layer
  config.py           every threshold that can change an outcome
  seed.py             two deterministic scenarios (approve / refer)
  bootstrap.py        create + catalog + seed in one call
  ontology/
    vocabulary.py     31 classes, 20 properties, 9 constraints, ICD-10 map, exclusions
    reasoner.py       subsumption, classification, exclusion matching, validation
    store.py          materialise the T-Box into insurance_ontology_*
  semantic/
    definitions.py    14 certified definitions (formula, version, owner, dependencies)
    engine.py         the single implementation of each definition
    catalog.py        persist/read the certified catalog
  kg/
    builder.py        project relational facts into typed nodes and named edges
    graph.py          adjacency graph, path queries, domain/range validation
  decision/
    context.py        assemble everything one adjudication may see
    rules.py          15 rules with severity and failure consequence
    engine.py         aggregate, persist decision + approval history + audit
    explain.py        deterministic explanation (text + structured sections)
    store.py          persist the rule catalog
  payments/gateway.py simulated direct deposit: authorise, settle, verify, replay
  api/                FastAPI router + standalone app (:3100)
  demo.py             the end-to-end run printed stage by stage
  tests/              56 unit + integration tests
```

Repository integration points that were changed (all additive):

| File | Change | Why |
| --- | --- | --- |
| `auto_ontology/server/__main__.py` | Mounts the claims router at `/api/insurance` when `INSURANCE_ENABLED=1` | Lets the platform serve claims beside the ontology API without forcing its schema on other deployments, and keeps the committed OpenAPI spec valid (the generator runs with the flag unset) |
| `alembic/env.py` | `target_metadata` is `[METADATA, INSURANCE_METADATA]` | Both catalogs share the default Postgres schema; without this, claims tables would read as drift and autogenerate would propose dropping them |
| `alembic/versions/c4a7f1b9d2e6_*.py` | New migration | Creates the 23 tables from the same metadata the code uses |
| `pyproject.toml` | unchanged | The domain needs only FastAPI, SQLAlchemy, Pydantic - all already dependencies |
| `README.md` | Pointer to this document | Discoverability |

---

## 2. Overall architecture

```mermaid
flowchart TB
    subgraph Client
        CW[Claimant / portal]
        RV[Claims reviewer]
        API_CLIENT[API client]
    end

    subgraph API["FastAPI - auto_ontology/insurance/api"]
        R1[POST /claims]
        R2[POST /claims/{id}/decision]
        R3[GET /claims/{id}/graph]
        R4[POST /claims/{id}/payments]
        R5[GET /claims/{id}/audit]
    end

    subgraph SVC["service.py - orchestration"]
        S1[submit_claim]
        S2[run_decision]
        S3[pay_claim]
    end

    subgraph DEC["Decision AI - decision/"]
        CTX[context.py<br/>assembles facts]
        RULES[rules.py<br/>15 rules]
        AGG[engine.py<br/>aggregate + persist]
        EXP[explain.py<br/>deterministic narrative]
    end

    subgraph SEM["Semantic layer - semantic/"]
        DEF[definitions.py<br/>14 certified definitions]
        ENG[engine.py<br/>one implementation each]
    end

    subgraph ONT["Ontology - ontology/"]
        VOC[vocabulary.py<br/>classes, properties, constraints]
        REA[reasoner.py<br/>subsumption, exclusions, validation]
    end

    subgraph KG["Knowledge graph - kg/"]
        BLD[builder.py<br/>facts -> nodes + edges]
        GRF[graph.py<br/>paths, domain/range checks]
    end

    subgraph DB["Relational store - schema.py (SQLite / Postgres)"]
        T1[(policies, parties,<br/>beneficiaries)]
        T2[(claims, certificates,<br/>evidence)]
        T3[(loans, exclusions,<br/>restrictions)]
        T4[(ontology + KG +<br/>semantic catalogs)]
        T5[(decisions, approvals,<br/>payments, audit)]
    end

    PAY[payments/gateway.py<br/>simulated direct deposit]

    CW --> R1 --> S1 --> T1
    API_CLIENT --> R2 --> S2 --> CTX
    RV --> R3
    CTX --> SEM
    CTX --> BLD
    CTX --> REA
    ENG --> DEF
    BLD --> GRF
    REA --> VOC
    SEM --> T3
    BLD --> T2
    CTX --> DEC
    RULES --> AGG --> EXP
    AGG --> T5
    S2 --> T4
    R4 --> S3 --> PAY --> T5
    R5 --> T5
```

**Read it as a dependency order.** Facts live in the relational tables; the
semantic layer computes numbers from them; the graph projects the same facts
into entities; the ontology supplies meaning and constraints; the decision
engine consumes all three; payments consume the decision. No arrow points
backwards, and no layer writes a fact another layer owns.

---

## 3. Semantic layer

### What problem it solves

The same business number is normally re-implemented in a rule, a stored
procedure, an API serializer and a report. They drift. Here "net death benefit"
exists once, with a version and an owner, and everything downstream reads it.

### Why it is required

A payout is an assertion. `$250,000` means nothing unless the reader knows it is
face amount minus outstanding loans, not minus premium arrears, and that it
excludes any legal restriction. The catalog states that; the engine implements
it; the decision records which version applied.

### Diagram

```mermaid
flowchart LR
    subgraph Catalog["definitions.py (certified)"]
        D1[policy_active_days<br/>FORMULA v1.0.0]
        D2[contestability_period_days<br/>PARAMETER]
        D3[outstanding_policy_loan_balance<br/>FORMULA]
        D4[net_death_benefit<br/>DERIVED]
        D5[total_primary_allocation_percent<br/>FORMULA]
        D6[payout_amount<br/>DERIVED]
        D7[claim_eligibility<br/>CATEGORICAL]
        D8[death_benefit_by_beneficiary<br/>DERIVED]
    end

    DB[(policy, claim, loan,<br/>beneficiary rows)] --> ENG[SemanticEngine.evaluate]
    Catalog --> ENG
    ENG --> V["SemanticValue<br/>value + display + formula<br/>+ version + inputs"]
    V --> DEC[Decision rules]
    V --> API[Decision response]
    V --> PAY[Payment amount check]
    V --> EXP[Explanation]
```

### Consumes / produces

| | |
| --- | --- |
| Consumes | claim date of death, policy dates/status/face amount/contestability days, loan status and balances, active designations |
| Produces | 14 `SemanticValue` records: typed value, display string, formula, kind, version, inputs, plus a fact bundle |

### Runtime

`SemanticEngine.evaluate(connection, claim_id)` loads the claim and policy,
computes in dependency order (validated acyclic at import), apportions the
benefit with the rounding residual to the last beneficiary, and returns the
values **and** the raw facts the rules need. The decision persists the whole
result, so this is reproducible without re-reading mutable rows.

**Deterministic.** Dates, money and percentages are never model-computed.

---

## 4. Ontology and knowledge graph

### What problem they solve

Rules written against ICD-10 codes rot. Rules written against *classes* do not:
`EX-SUICIDE-2Y` names `lio:SelfInflictedDeath`, and an `X71` record matches by
inference. The graph answers "how is this claimant connected to this insured,
and what proves it" with a path, not a join the reviewer must trust.

### Diagram

```mermaid
flowchart TB
    subgraph TB["T-Box - ontology/vocabulary.py"]
        C1[Party]
        C2[Person]
        C3[InsuredPerson]
        C4[Beneficiary]
        C5[PrimaryBeneficiary]
        C6[Spouse]
        C7[LifeInsurancePolicy]
        C8[InsuranceClaim]
        C9[DeathCertificate]
        C10[CauseOfDeath]
        C11[NaturalDeath]
        C12[CardiovascularDeath]
        C13[AccidentalDeath]
        C14[SelfInflictedDeath]
        C15[PolicyLoan]
        C16[PolicyExclusion]
        C17[LegalRestriction]
        C1 --> C2 --> C3
        C2 --> C4 --> C5
        C2 --> C6
        C10 --> C11 --> C12
        C10 --> C13
        C10 --> C14
    end

    ICD["ICD-10 I21.9"] -->|classify_cause| C12
    EXC["Exclusion EX-AVIATION<br/>excludes lio:AviationDeath"] -->|subsumption test| C13

    subgraph AB["A-Box - kg/builder.py"]
        N1((InsuredPerson<br/>Robert Hayes))
        N2((LifeInsurancePolicy<br/>TL-2021-004821))
        N3((PrimaryBeneficiary<br/>Jane Hayes))
        N4((DeathCertificate<br/>DC-2026-551234))
        N5((CardiovascularDeath<br/>I21.9))
        N1 -->|coveredBy| N2
        N2 -->|hasBeneficiary| N3
        N3 -->|spouseOf| N1
        N4 -->|certifiesDeathOf| N1
        N4 -->|hasCauseOfDeath| N5
    end

    TB -.types.-> AB
    AB --> VAL[domain/range validation]
    AB --> PATH[path query:<br/>insured -> policy -> beneficiary]
    PATH --> DEC[Decision evidence]
    VAL --> DEC
```

### Consumes / produces

| | |
| --- | --- |
| Ontology consumes | the vocabulary (code) |
| Ontology produces | classification + ancestor chain, applicable exclusions, 9 constraint results |
| Graph consumes | claim, policy, parties, designations, certificate, cause, evidence, loans, exclusions, restrictions, competing claims |
| Graph produces | typed nodes and named edges, the insured→policy→beneficiary path, ontology violations, readable triples |

### Runtime

`build_context` loads facts, projects the graph, persists it (delete-then-insert
for that claim), then validates domain/range for every edge. Nodes carry
`ontology_types`, which is how one person can be both `PrimaryBeneficiary` and
`Spouse` without a duplicate "spouse node". A `sameAs` edge links the claimant
node to the beneficiary node when they are the same party.

**Deterministic.** The ontology is authored code; inference is a closure over
declared parents. Auto-Ontology's LLM-driven compilation is what *proposes* terms
and relationships upstream; this layer certifies and applies them.

---

## 5. Decision AI

### What problem it solves

Given contractual facts, evidence and obligations, produce one of three
outcomes - approve, refer, deny - with a reason per rule, a payout, and an audit
reference that survives scrutiny. It must be the same answer on a re-run.

### Diagram

```mermaid
flowchart TB
    A[Claim submitted] --> B{Decision requested}
    B --> C[Build context]
    C --> C1[Semantic calculations]
    C --> C2[Ontology validation]
    C --> C3[Knowledge graph + path]
    C1 --> D[Evaluate 15 rules<br/>priority order]
    C2 --> D
    C3 --> D
    D --> E{Any DENY-severity failure?}
    E -->|yes| F[DENIED<br/>payout 0]
    E -->|no| G{Any REFER failure?}
    G -->|yes| H[MANUAL_REVIEW<br/>payout 0, reasons listed]
    G -->|no| I{All pass?}
    I -->|yes| J[AUTO_APPROVED<br/>payout = semantic payout]
    F --> K[Persist: decision,<br/>approval history, claim status,<br/>hash-chained audit event]
    H --> K
    J --> K
    K --> L[Explanation text + sections]
```

### The rules

| ID | Category | Consequence on failure | Checks |
| --- | --- | --- | --- |
| R01 | SEMANTIC | REFER | Policy in force on date of death |
| R02 | SEMANTIC | REFER | Active days > contestability period |
| R03 | ONTOLOGY | REFER | Cause resolves to a class (not undetermined) |
| R04 | ONTOLOGY | **DENY** | No active exclusion covers the cause class |
| R05 | KNOWLEDGE_GRAPH | REFER | Claim, policy and certificate name the same insured |
| R06 | EVIDENCE | REFER | Claim and certificate record the same cause |
| R07 | SEMANTIC | REFER | Exactly one primary beneficiary, 100% allocated |
| R08 | KNOWLEDGE_GRAPH | REFER | Beneficiary verified, relationship proven |
| R09 | SEMANTIC | **DENY** | Net death benefit > 0 |
| R10 | KNOWLEDGE_GRAPH | REFER | No active legal or payment restriction |
| R11 | EVIDENCE | REFER | All required evidence types verified |
| R12 | KNOWLEDGE_GRAPH | REFER | No competing claim on the policy |
| R13 | KNOWLEDGE_GRAPH | REFER | Evidence path exists; no ontology violations |
| R14 | LIMIT | REFER | Payout within straight-through limit |
| R15 | SEMANTIC | REFER | Payout reconciles to benefit x allocation |

Only two outcomes deny, and both are contractual: an applicable exclusion or a
fully encumbered policy. Everything uncertain refers to a human.

### Consumes / produces

| | |
| --- | --- |
| Consumes | semantic values, ontology results, graph + path, evidence, restrictions, settings |
| Produces | `decision_result` row (status, payout, rule outcomes, three evidence payloads, explanation, audit ref), `claim_approval_history` row, claim status, audit event |

### Runtime

`decide_claim` assembles the context, runs rules, aggregates, builds the
explanation, then writes all rows in one transaction. A re-run marks the
previous decision `is_current = false` and writes a new one - the record of a
re-adjudication, not an edit.

---

## 6. End-to-end sequence

```mermaid
sequenceDiagram
    actor C as Claimant
    participant API as Claims API
    participant S as service.py
    participant SEM as Semantic engine
    participant KG as Graph builder
    participant ONT as Ontology reasoner
    participant DEC as Decision engine
    participant PAY as Payment gateway
    participant DB as Database

    C->>API: POST /claims (policy, cause, certificate, evidence)
    API->>S: submit_claim
    S->>DB: claim + evidence + audit event
    API-->>C: 201 claim SUBMITTED

    Note over DEC: Adjudication
    API->>S: POST /claims/{id}/decision
    S->>DEC: decide_claim
    DEC->>SEM: evaluate definitions
    SEM-->>DEC: 14 values (1,825 days, $250,000 net)
    DEC->>KG: project graph for claim
    KG-->>DEC: nodes/edges + insured->policy->beneficiary path
    DEC->>ONT: validate constraints + match exclusions
    ONT-->>DEC: 9 constraints SATISFIED, no exclusion applies
    DEC->>DEC: 15 rules PASS
    DEC->>DB: decision AUTO_APPROVED ($250,000) + approval history + audit
    DEC-->>API: outcome + explanation
    API-->>C: 200 AUTO_APPROVED

    Note over PAY: Settlement
    API->>PAY: POST /claims/{id}/payments (idempotency key, approver)
    PAY->>DB: re-read decision + verify beneficiary designation
    PAY->>DB: AUTHORIZED -> SETTLED, claim PAID, audit events
    API-->>C: 201 SETTLED

    API->>PAY: POST /payments/{id}/verify
    PAY->>DB: amount, beneficiary, settlement, audit chain
    PAY-->>API: VERIFIED
```

---

## 7. What the running scenario produces

```
LIFE INSURANCE CLAIMS DECISION AI
Bootstrap: 31 ontology classes, 20 relationships, 14 semantic definitions, 15 decision rules
Claim CLM-2026-0001 | policy TL-2021-004821 | 20-Year Level Term Life | $250,000.00
Insured died 2026-06-14 of I21.9 (Acute myocardial infarction)
Beneficiary: Jane Marie Hayes (SPOUSE, PRIMARY, 100.00%)

STEP 1 - SEMANTIC LAYER
  Policy Active Days                     1,825 days   [FORMULA, v1.0.0]
  Contestability Period                    730 days   [PARAMETER, v1.0.0]
  Past Contestability Period                   true   [DERIVED, v1.0.0]
  Policy Face Amount                    $250,000.00   [PARAMETER, v1.0.0]
  Outstanding Policy Loans                    $0.00   [FORMULA, v1.0.0]
  Net Death Benefit                     $250,000.00   [DERIVED, v1.0.0]
  Total Primary Allocation                  100.00%   [FORMULA, v1.0.0]
  Claim Payout Amount                   $250,000.00   [DERIVED, v1.0.0]
  Claim Eligibility                        ELIGIBLE   [CATEGORICAL, v1.0.0]

STEP 2 - ONTOLOGY
  Inference: I21.9 => CardiovascularDeath subClassOf NaturalDeath subClassOf CauseOfDeath
  C8_EXCLUSION_FREE  SATISFIED  No active exclusion covers CardiovascularDeath

STEP 3 - KNOWLEDGE GRAPH
  Nodes 17 | Edges 24
  Required chain: InsuredPerson Robert James Hayes --coveredBy-->
    LifeInsurancePolicy TL-2021-004821 --hasBeneficiary--> PrimaryBeneficiary Jane Marie Hayes
  Ontology domain/range violations: 0

STEP 4 - DECISION AI
  STATUS: AUTO_APPROVED
  Net payout: $250,000.00 USD
  Confidence (rules passed / applicable): 1.00
  Audit reference: AUD-95F6604A72F021C0
  PASS R01 ... PASS R15

STEP 5 - SIMULATED PAYMENT
  Payment PAY-C0980D58E8E9 | SETTLED
  Amount $250,000.00 USD via SIMULATED_ACH_DIRECT_DEPOSIT
  Human approval required: True (approved_by=officer:j.mensah)
  Retry with the same idempotency key returned the same payment (idempotent_replay=True)
  Verification: VERIFIED

STEP 6 - AUDIT
  Chain intact: True | events: 4

STEP 7 - COUNTER-EXAMPLE
  Claim CLM-2002: MANUAL_REVIEW
  FAIL R10_NO_LEGAL_RESTRICTIONS: COURT_ORDER ... pending probate
  Payment attempt refused: only AUTO_APPROVED claims may be paid
```

Run it yourself:

```bash
uv run python -m auto_ontology.insurance.demo
```

Artifacts (full JSON) are written to `demo_output/`:
`decision_auto_approved.json`, `decision_manual_review.json`,
`payment_settled.json`, `knowledge_graph.json`.

---

## 8. API

Standalone app on port 3100 (`python -m auto_ontology.insurance.api`);
interactive OpenAPI at `/docs`. When mounted on the platform
(`INSURANCE_ENABLED=1`), the same routes appear under `/api/insurance`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness, engine version, limits, simulation notice |
| GET | `/ontology` | T-Box classes, properties, constraints, cause and exclusion maps |
| GET | `/semantic/definitions` | Certified definitions with formulas and versions |
| GET | `/rules` | Persisted decision-rule catalog |
| POST | `/claims` | Submit a death claim with evidence |
| GET | `/claims` | List claims with decision status |
| GET | `/claims/{id}` | Claim, policy, beneficiaries, evidence, decision, payments |
| POST | `/claims/{id}/decision` | Adjudicate and persist |
| GET | `/claims/{id}/decision` | Current decision with explanation |
| GET | `/claims/{id}/graph` | Knowledge graph, rebuilt from current facts |
| GET | `/claims/{id}/audit` | Audit events + hash-chain verification |
| POST | `/claims/{id}/payments` | Authorise and settle the simulated payout |
| GET | `/claims/{id}/payments` | Payments for the claim |
| GET | `/payments/{id}` | One payment |
| POST | `/payments/{id}/verify` | Reconcile against decision, designation, audit chain |
| GET | `/policies/{id}/semantics` | Certified values for a policy's latest claim event |

### Submit a claim

```bash
curl -X POST http://localhost:3100/api/insurance/claims \
  -H "Content-Type: application/json" \
  -d '{
    "policy_id": "POL-1001",
    "claimant_party_id": "PRT-1002",
    "date_of_death": "2026-06-14T00:00:00",
    "cause_code": "I21.9",
    "death_certificate_id": "DC-9001",
    "evidence": [
      {"evidence_type": "DEATH_CERTIFICATE", "status": "VERIFIED"},
      {"evidence_type": "CLAIM_FORM", "status": "PENDING"}
    ]
  }'
```

```json
{
  "claim": {
    "claim_id": "CLM-9F2A1C3D4E5B",
    "claim_number": "CLM-2026-0003",
    "status": "SUBMITTED",
    "date_of_death": "2026-06-14T00:00:00",
    "cause_code": "I21.9",
    "cause_of_death": {
      "class_iri": "lio:CardiovascularDeath",
      "category": "NATURAL",
      "inference": "I21.9 (...) => CardiovascularDeath subClassOf NaturalDeath subClassOf CauseOfDeath"
    }
  },
  "policy": {"policy_number": "TL-2021-004821", "face_amount": "250000.00"},
  "beneficiaries": [
    {"beneficiary_id": "BEN-3001", "name": "Jane Marie Hayes",
     "relationship": "SPOUSE", "designation_type": "PRIMARY",
     "allocation_percent": "100.00", "verification_status": "VERIFIED"}
  ],
  "evidence": [{"evidence_type": "CLAIM_FORM", "status": "PENDING"}],
  "decision": null,
  "payments": []
}
```

### Decide

```bash
curl -X POST http://localhost:3100/api/insurance/claims/CLM-2001/decision \
  -H "Content-Type: application/json" -d '{}'
```

Response (trimmed to the decision's contract; `semantic_calculations`,
`ontology_validation_results`, `knowledge_graph_evidence` and
`decision_rules_applied` are full arrays):

```json
{
  "claim_id": "CLM-2001",
  "policy_id": "POL-1001",
  "decision_id": "DEC-3B9E12A4C7D0",
  "approval_status": "AUTO_APPROVED",
  "net_payout_amount": "250000.00",
  "currency": "USD",
  "confidence_score": 1.0,
  "audit_reference": "AUD-95F6604A72F021C0",
  "engine_version": "1.0.0",
  "rules_version": "1.0.0",
  "decision_path": "AUTO_APPROVED: all 15 rules passed (policy, ontology, graph, evidence, and limit checks).",
  "semantic_calculations": {
    "definitions_evaluated": 14,
    "values": [
      {"name": "policy_active_days", "value": 1825, "display_value": "1,825 days",
       "definition_kind": "FORMULA", "definition_version": "1.0.0",
       "formula": "(date_of_death - policy.effective_date).days"},
      {"name": "net_death_benefit", "value": "250000.00", "display_value": "$250,000.00",
       "definition_kind": "DERIVED", "definition_version": "1.0.0"}
    ]
  },
  "ontology_validation_results": [
    {"constraint": "C8_EXCLUSION_FREE", "status": "SATISFIED",
     "evidence": "No active exclusion covers CardiovascularDeath (contestability passed)."}
  ],
  "knowledge_graph_evidence": {
    "node_count": 17,
    "edge_count": 24,
    "evidence_chain_text": "InsuredPerson Robert James Hayes --coveredBy--> LifeInsurancePolicy TL-2021-004821 --hasBeneficiary--> PrimaryBeneficiary Jane Marie Hayes",
    "ontology_violations": []
  },
  "decision_explanation": "Claim CLM-2026-0001 under policy TL-2021-004821 is AUTO_APPROVED for $250,000.00, payable to Jane Marie Hayes ...",
  "decided_by": "system:decision-engine"
}
```

### Pay and verify

```bash
curl -X POST http://localhost:3100/api/insurance/claims/CLM-2001/payments \
  -H "Content-Type: application/json" \
  -d '{"idempotency_key": "claim-CLM-2001-payout-1", "approver_id": "officer:j.mensah"}'

curl -X POST http://localhost:3100/api/insurance/payments/PAY-.../verify
```

```json
{
  "payment_id": "PAY-C0980D58E8E9",
  "claim_id": "CLM-2001",
  "amount": "250000.00",
  "currency": "USD",
  "method": "SIMULATED_ACH_DIRECT_DEPOSIT",
  "status": "SETTLED",
  "idempotent_replay": false,
  "authorization_reference": "AUTH-6E96F7D480BA6D20",
  "provider_reference": "SIM-ACH-D48D5D2535A2",
  "account": "****FBD6",
  "requires_human_approval": true,
  "approved_by": "officer:j.mensah",
  "verification_status": "PENDING",
  "simulation_notice": "SIMULATED PAYMENT - no funds moved. Provider references are synthetic."
}
```

**Payment controls**

* only a current `AUTO_APPROVED` decision is payable (409 otherwise);
* above `INSURANCE_PAYMENT_HUMAN_APPROVAL_LIMIT` (default $100,000) a named
  `approver_id` is required, else 403 - a second, independent control from the
  claim decision;
* the same idempotency key with the same details returns the original
  transaction (`idempotent_replay: true`); different details are a 409;
* a second payment for the same claim is refused;
* settlement and verification are separate states; verification re-checks the
  amount, the designation, the settlement and the hash-chained audit trail.

**Errors:** 404 unknown claim/payment; 409 manual-review claim, duplicate
payment, idempotency conflict; 403 missing approver; 422 unknown ICD-10 code,
missing certificate, surrendered policy.

---

## 9. Running and testing

### Fastest path (no Docker, no NVIDIA key)

The claims domain needs only FastAPI, SQLAlchemy, Pydantic, Uvicorn, pytest and
httpx. The platform's full dependency set additionally requires a C++ toolchain
on Windows (`twofish`, via `pyjks`), so the standalone demo uses a small venv:

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe fastapi uvicorn sqlalchemy pydantic pytest httpx
# macOS/Linux: --python .venv/bin/python

.venv/Scripts/python -m auto_ontology.insurance.demo
.venv/Scripts/python -m pytest auto_ontology/insurance/tests -q
```

Expected: `56 passed`.

The same suite runs against Postgres. The database it names is dropped and
recreated for every test, so point it at a disposable instance:

```bash
INSURANCE_TEST_DB_URL=postgresql+psycopg://postgres:secret@127.0.0.1:5432/insurance_test \
  .venv/Scripts/python -m pytest auto_ontology/insurance/tests -q
```

`.github/workflows/ci-insurance.yml` runs that on every change to the domain,
against a `pgvector/pgvector:pg17` service: it applies the migrations, fails on
schema drift, asserts the table set equals `INSURANCE_METADATA` at a single head
revision, runs the suite on Postgres, then runs the demo and checks its
artifacts. The single-head assertion is deliberate — a revision parented onto
the wrong commit leaves two heads, and `upgrade head` then refuses to run at all.

### Serve the API

```bash
.venv/Scripts/python -m auto_ontology.insurance.api
# http://127.0.0.1:3100/      the claims console
# http://127.0.0.1:3100/docs OpenAPI UI
```

The console at `/` is one static page served by the app itself, laid out like
the NVIDIA Auto-Ontology UI: an icon rail (Chat, Discovery, Terms, Analysis,
Exploration, Data, Analytics, Settings), a catalog panel and breadcrumbs. It
drives the same `/api/insurance` routes a caller would.

- **Chat** asks the database, ontology and semantic layer a question and shows
the answer, the rows and the SQL used.
- **Data** lists all 23 tables by layer, then columns, foreign keys, row counts
and sample rows.
- **Terms** browses the 14 certified semantic definitions (formula, unit,
owner, version, dependencies) and the 31 ontology classes with constraints.
- **Analysis** runs the decision engine, lists all 15 rules, pays (simulated),
verifies and shows the audit chain.
- **Exploration** runs read-only SQL; **Analytics** and **Discovery** summarise
claims, decisions, payments, rules and causes.

The catalog endpoints behind those pages are also public API:

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/insurance/data` | Tables grouped by layer, with row counts |
| GET | `/api/insurance/data/{table}` | Columns, keys, relationships, sample rows |
| POST | `/api/insurance/sql` | Read-only `SELECT` result (DDL/DML refused) |
| POST | `/api/insurance/ask` | Deterministic answer, table and SQL for a question |
| GET | `/api/insurance/analytics` | Claim, decision, payment, rule and audit counts |

No build step and no Node dependency, so the console works wherever the
standalone API does.

Seeding happens on startup (`INSURANCE_SEED_ON_START=1`). Create tables without
seeding via `INSURANCE_SEED_ON_START=0`; bootstrap a database explicitly with:

```bash
.venv/Scripts/python -m auto_ontology.insurance.bootstrap
```

### Full platform

```bash
uv sync                       # needs build tools on Windows
uv run alembic upgrade head   # creates the 23 claims tables in Postgres
INSURANCE_ENABLED=1 \
INSURANCE_DB_URL=postgresql+psycopg://user:pass@host:5432/auto_ontology \
  uv run python -m auto_ontology.server
```

The claims routes then appear at `http://localhost:3001/api/insurance` next to
the ontology API, behind the same gateway.

### Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `INSURANCE_DB_URL` | `sqlite:///./insurance_demo.db` | Storage. Point at Postgres in a deployment |
| `INSURANCE_AUTO_APPROVAL_LIMIT` | `500000` | Payout above this is referred (R14) |
| `INSURANCE_PAYMENT_HUMAN_APPROVAL_LIMIT` | `100000` | Payment above this needs a named approver |
| `INSURANCE_CONTESTABILITY_DAYS` | `730` | Fallback only; each policy stores its own |
| `INSURANCE_SEED_ON_START` | `1` | Seed the two sample scenarios at API startup |
| `INSURANCE_ENABLED` | unset | Mount the claims router on the platform API |
| `INSURANCE_API_PORT` | `3100` | Standalone port |
| `INSURANCE_TEST_DB_URL` | `sqlite://` | Database the test suite resets per test |

### Verified on Postgres

```bash
docker run -d --name insurance-pg -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=insurance_test -p 5432:5432 pgvector/pgvector:pg17

POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_USER=postgres \
POSTGRES_PASSWORD=secret POSTGRES_DATABASE=insurance_test \
  .venv/Scripts/alembic upgrade head          # platform chain + claims domain

docker exec insurance-pg psql -U postgres -d insurance_test \
  -c "select count(*) from information_schema.tables \
      where table_name like 'insurance_%';"    # 23
alembic check                                  # no new upgrade operations
```

---

## 10. AI-driven versus deterministic

This boundary is the point of the architecture, not a disclaimer.

| Stage | Nature | Why |
| --- | --- | --- |
| Reading source schemas, proposing Terms, joins and SQL attributes (Auto-Ontology's `semantic/` pipeline) | **AI-assisted** (NIM LLM + deterministic BFS + embedding retrieval) | Language understanding and schema disambiguation at scale |
| This domain's ontology vocabulary and ICD-10 map | **Authored, deterministic** | Domain assertions must be reviewable and versioned, not sampled |
| Causal classification, exclusion matching, constraint checks | **Deterministic inference** | Same facts must give the same meaning every time |
| Active days, loan balance, net benefit, payout, allocation | **Deterministic arithmetic** | Money |
| Rule outcomes, aggregation, explanation text | **Deterministic** | A regulator may ask why this claim and not another |
| Payment authorisation, settlement, verification | **Deterministic simulation** | Idempotency and audit |

No model call is made anywhere in `insurance/`. The AI contribution is upstream
(ontology generation) and would be downstream only in optional, non-binding
places: drafting a claimant letter from the structured sections, or triaging
which referred claims a reviewer should open first. Neither may change a number
or an outcome.

---

## 11. Limitations and production hardening

* The migration was **executed against PostgreSQL 17.11**
  (`pgvector/pgvector:pg17`): the full chain upgrades to head, creates the 23
  tables, `alembic check` reports no drift, `downgrade -1` removes exactly those
  23 tables and `upgrade head` recreates them. The 56 tests and the complete
  claim scenario also pass on Postgres, with identical amounts, graph and audit
  reference. Dialect notes are in `docs/insurance/data-model.md`.
* Authentication is the platform gateway's job. The standalone app has none;
  `actor` fields come from the request body and must be overwritten from the
  authenticated identity before any real deployment.
* The demo's `POST /demo/reset` drops data. Remove it from a public deployment.
* Payments are simulated. A real rail needs: beneficiary bank verification,
  sanctions screening, tax withholding, reversal handling, and a provider
  webhook for asynchronous settlement instead of immediate `SETTLED`.
* The ontology covers an illustrative slice of life insurance. Real use needs a
  full ICD-10 map, state-specific contestability rules, product-specific
  exclusions, and a review workflow for referred claims (the approval-history
  table already records `PENDING_HUMAN`).
* `confidence_score` is "share of applicable rules that passed", not a
  probability. It is deliberately not a model score.

---

## 12. Where each fact lives

| Question | Answer lives in | Not in |
| --- | --- | --- |
| Who is insured, what does the policy pay, which evidence was verified | 23 relational tables (`schema.py`) | graph, JSON payloads |
| What does "net death benefit" mean, and how is it computed | `semantic/definitions.py` + `insurance_semantic_definition` | rules, API code |
| What kinds of things exist and how may they relate | `ontology/vocabulary.py` + `insurance_ontology_*` | graph rows |
| Which things exist for this claim and how they connect | `kg/builder.py` -> `insurance_kg_*` | relational tables (they hold facts, not edges) |
| Why was this claim approved | `insurance_decision_result` + `insurance_claim_approval_history` + `insurance_audit_event` | logs |

Avoiding duplication: a person's name exists once (`insurance_party`); roles
reference it. An allocation lives on the designation, not the person. The graph
stores no new facts, only typed projections of rows. The semantic layer stores
definitions, never values. See [`data-model.md`](./data-model.md) for each table.

---

## 13. Learning path

Read in this order to see how the pieces cooperate:

1. `semantic/definitions.py` - what a number means.
2. `semantic/engine.py` - how it is computed once, and what the decision receives.
3. `ontology/vocabulary.py` - the business's assertions about meaning.
4. `ontology/reasoner.py` - inference: how `I21.9` becomes "not an accident".
5. `kg/builder.py` - why the graph adds no facts but adds connection.
6. `kg/graph.py` - path queries and ontology alignment.
7. `decision/context.py` - the contract between layers.
8. `decision/rules.py` - policy decisions, separated from calculations.
9. `decision/engine.py` - aggregation, persistence, audit.
10. `payments/gateway.py` - controls around an irreversible action.
