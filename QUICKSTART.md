<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: run the claims decision AI

This walks the life insurance claims domain in `auto_ontology/insurance/` from a
cold checkout to a paid claim and a verified audit trail. It needs no NVIDIA key,
no model call and no Postgres for the first two sections; everything is
deterministic and **payments are simulated**.

- Architecture and layer-by-layer explanation: [`docs/insurance/README.md`](./docs/insurance/README.md)
- The 23 tables and what belongs in each layer: [`docs/insurance/data-model.md`](./docs/insurance/data-model.md)

## 0. What you need

- Python 3.12. This repository's `.venv` already has the standalone app's
  dependencies, so most people can skip the install.
- Docker only for the Postgres section (3d).

If you need to rebuild the environment:

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe fastapi uvicorn sqlalchemy pydantic pytest httpx
# macOS/Linux: --python .venv/bin/python
```

Add `alembic psycopg[binary] python-dotenv` for the Postgres path. Do not use
`uv sync` on Windows unless you have MSVC build tools: it fails building
`twofish` (pulled in by `pyjks` via the Snowflake/Kyuubi dependencies). On
Linux and in CI, `uv sync` is the documented install.

Commands below assume the repository root as the working directory and Git Bash
syntax; PowerShell variants are given where the syntax differs.

## 1. Run the scenario without a server

```bash
.venv/Scripts/python -m auto_ontology.insurance.demo
```

This drops and reseeds `insurance_demo.db` (or whatever `INSURANCE_DB_URL`
points at), walks claim `CLM-2001` through all five layers, then decides the
restricted `CLM-2002`. Check these as they print:

| Stage | Expected |
| --- | --- |
| Semantic layer | 1,825 active days; contestability 730; net benefit $250,000.00 |
| Ontology | 9/9 constraints satisfied, 0 exclusions matched |
| Knowledge graph | 17 nodes / 24 edges, 0 domain/range violations |
| Decision | `AUTO_APPROVED`, $250,000.00 USD, 15/15 rules pass, `AUD-95F6604A72F021C0` |
| Payment | `SETTLED`, then `VERIFIED` (5 checks) |
| Audit | hash chain intact, 4 events |
| Counter-example | `CLM-2002` → `MANUAL_REVIEW`, payout $0.00 (court order) |

JSON artifacts are written to `demo_output/`; they are the evidence for the API
response shapes.

## 2. Serve the API

```bash
.venv/Scripts/python -m auto_ontology.insurance.api
```

Then open **http://127.0.0.1:3100/** for the claims console: claim and policy
records, the decision engine's 15 rules and 9 constraints, semantic
calculations, the knowledge graph, simulated payments with idempotency and
verification, the audit hash chain, the ontology and semantic catalogs, and a
one-click scenario runner. Raw JSON is available under each panel, and the
OpenAPI UI is at **http://127.0.0.1:3100/docs**.

PowerShell:

```powershell
.\.venv\Scripts\python.exe -m auto_ontology.insurance.api
```

Seeding at startup is on by default. To serve an existing database untouched,
set `INSURANCE_SEED_ON_START=0`; to move off port 3100, set
`INSURANCE_API_PORT`. The standalone app reads `os.environ` directly — it does
**not** load `.env` — so pass variables in the shell:

```bash
INSURANCE_API_PORT=3101 INSURANCE_SEED_ON_START=0 \
  .venv/Scripts/python -m auto_ontology.insurance.api
```

```powershell
$env:INSURANCE_API_PORT = "3101"
$env:INSURANCE_SEED_ON_START = "0"
.\.venv\Scripts\python.exe -m auto_ontology.insurance.api
```

If you ran section 1, this database already has `CLM-2001` decided and paid.
Deciding it again is refused (`HTTP 422: already paid; a new decision would not
be payable`) until you reset, which section 3b does first.

Routes are under `/api/insurance`: `health`, `ontology`, `semantic/definitions`,
`rules`, `claims`, `claims/{id}`, `claims/{id}/decision`, `claims/{id}/graph`,
`claims/{id}/audit`, `claims/{id}/payments`, `policies/{id}/semantics`,
`payments/{id}`, `payments/{id}/verify`, `demo/reset`, `demo/bootstrap`.
The full variable list is in
[`docs/insurance/README.md`](./docs/insurance/README.md#configuration).

## 3. Test the data

### 3a. Automated suite

```bash
.venv/Scripts/python -m pytest auto_ontology/insurance/tests -q
```

Expected: `56 passed` in a few seconds (in-memory SQLite). The same suite runs
on Postgres — see 3d.

### 3b. Walk the claim over HTTP

With the API running, start from the sample data so the walkthrough is
repeatable — this is required if you ran section 1, whose database already has
`CLM-2001` paid:

```bash
curl -s -X POST http://127.0.0.1:3100/api/insurance/demo/reset

curl -s http://127.0.0.1:3100/api/insurance/health

curl -s -X POST http://127.0.0.1:3100/api/insurance/claims/CLM-2001/decision \
  -H "Content-Type: application/json" -d '{"actor":"officer:j.mensah"}'
```

Expect `approval_status: AUTO_APPROVED`, `net_payout_amount: "250000.00"`,
15 rules all `PASS`, and `knowledge_graph_evidence` reporting 17 nodes / 24
edges. Then pay it, twice, to see idempotency. `idempotency_key` must be at least 8
characters — the API validates that before it reaches the gateway:

```bash
# keep the payment_id from this response
curl -s -X POST http://127.0.0.1:3100/api/insurance/claims/CLM-2001/payments \
  -H "Content-Type: application/json" \
  -d '{"idempotency_key":"claim-CLM-2001-payout-1","approver_id":"officer:j.mensah"}'

# same call again: same payment_id, idempotent_replay: true
curl -s -X POST http://127.0.0.1:3100/api/insurance/claims/CLM-2001/payments \
  -H "Content-Type: application/json" \
  -d '{"idempotency_key":"claim-CLM-2001-payout-1","approver_id":"officer:j.mensah"}'

curl -s -X POST http://127.0.0.1:3100/api/insurance/payments/<payment_id>/verify
curl -s http://127.0.0.1:3100/api/insurance/claims/CLM-2001/audit
curl -s http://127.0.0.1:3100/api/insurance/claims/CLM-2001
```

Expect `verification_status: VERIFIED` with `audit_chain.intact: true`; audit
`intact: true` with 4 events; claim status `PAID`. Then the refusal:

```bash
curl -s -X POST http://127.0.0.1:3100/api/insurance/claims/CLM-2002/decision \
  -H "Content-Type: application/json" -d '{}'                       # MANUAL_REVIEW

curl -i -X POST http://127.0.0.1:3100/api/insurance/claims/CLM-2002/payments \
  -H "Content-Type: application/json" \
  -d '{"idempotency_key":"claim-CLM-2002-payout-1","approver_id":"officer:j.mensah"}'  # HTTP 409
```

The same two calls in PowerShell — note `curl` is aliased to
`Invoke-WebRequest`, so use `Invoke-RestMethod` (or `curl.exe`):

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:3100/api/insurance/claims/CLM-2001/decision `
  -ContentType 'application/json' -Body '{"actor":"officer:j.mensah"}'

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:3100/api/insurance/claims/CLM-2001/payments `
  -ContentType 'application/json' `
  -Body '{"idempotency_key":"claim-CLM-2001-payout-1","approver_id":"officer:j.mensah"}'
```

### 3c. Read the stored data

Money is stored as integer cents by design, so $250,000 reads as `25000000`:

```bash
.venv/Scripts/python - <<'PY'
import sqlite3

db = sqlite3.connect("insurance_demo.db")
print("claims tables:",
      db.execute("select count(*) from sqlite_master "
                 "where type='table' and name like 'insurance_%'").fetchone()[0])
print("decisions:",
      db.execute("select claim_id, status, net_payout_amount, audit_reference "
                 "from insurance_decision_result where is_current=1").fetchall())
print("payments:",
      db.execute("select id, status, amount, provider_reference "
                 "from insurance_payment_transaction").fetchall())
print("audit events:",
      db.execute("select action, event_hash from insurance_audit_event "
                 "order by occurred_at").fetchall())
PY
```

### 3d. Run it on Postgres

```bash
docker run -d --name insurance-pg -e POSTGRES_PASSWORD=insurance_test \
  -e POSTGRES_DB=insurance_test -p 55432:5432 pgvector/pgvector:pg17

POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=postgres \
POSTGRES_PASSWORD=insurance_test POSTGRES_DATABASE=insurance_test \
  .venv/Scripts/alembic upgrade head        # platform chain + 23 claims tables

POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=postgres \
POSTGRES_PASSWORD=insurance_test POSTGRES_DATABASE=insurance_test \
  .venv/Scripts/alembic check               # "No new upgrade operations detected"

INSURANCE_DB_URL=postgresql+psycopg://postgres:insurance_test@127.0.0.1:55432/insurance_test \
INSURANCE_SEED_ON_START=1 \
  .venv/Scripts/python -m auto_ontology.insurance.api
```

PowerShell sets the same variables with `$env:POSTGRES_HOST = "127.0.0.1"` and so
on before each command (Alembic reads `POSTGRES_*`; the domain reads
`INSURANCE_DB_URL`).

Run the suite there too. The tests **drop and recreate the schema per test**, so
point this only at a disposable database:

```bash
INSURANCE_TEST_DB_URL=postgresql+psycopg://postgres:insurance_test@127.0.0.1:55432/insurance_test \
  .venv/Scripts/python -m pytest auto_ontology/insurance/tests -q
```

Clean up with `docker rm -f insurance-pg`.

## 4. Reset state

Delete `insurance_demo.db` and `demo_output/`, or, with the API running:

```bash
curl -s -X POST http://127.0.0.1:3100/api/insurance/demo/reset
```

A deployment that exposes this router publicly should remove that route: it
drops every claims row.

## 5. Troubleshooting

| Symptom | Cause |
| --- | --- |
| Port 3100 already in use | Another API is running; set `INSURANCE_API_PORT` |
| `no such table: insurance_claim` | `INSURANCE_SEED_ON_START=0` against an empty database; run the demo, or `python -m auto_ontology.insurance.bootstrap` |
| Payment returns 409 | The claim has no current `AUTO_APPROVED` decision; decide it first |
| Decide returns 422 `already paid` | The claim was settled earlier (e.g. by the demo); `POST /api/insurance/demo/reset` |
| `alembic` says multiple heads | A revision was chained onto the wrong parent; run `alembic heads` and merge before upgrading |
| `uv sync` fails on `twofish` | Windows without MSVC build tools; use the light `uv pip install` in section 0 |
| `INSURANCE_ENABLED=1` server won't start | The full platform needs the full dependency set; use the standalone API on Windows |
