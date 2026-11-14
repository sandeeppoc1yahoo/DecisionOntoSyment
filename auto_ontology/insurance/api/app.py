# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Standalone claims API, and the mount point for the platform.

Two deployment shapes:

* **Standalone** - ``python -m auto_ontology.insurance.api`` serves this app on
  port 3100 against ``INSURANCE_DB_URL`` (SQLite by default). The demo and the
  tests use it; nothing else in the platform is needed.
* **Mounted** - ``auto_ontology.server.create_app`` includes the same router at
  ``/api/insurance``, so the claims surface appears beside the ontology and chat
  APIs under the platform's authentication gateway. Point
  ``INSURANCE_DB_URL`` at the platform's Postgres in that deployment.
"""

from __future__ import annotations

import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from auto_ontology.insurance import __version__
from auto_ontology.insurance.api.router import router as insurance_router
from auto_ontology.insurance.bootstrap import bootstrap
from auto_ontology.insurance.db import create_schema, dispose_engine

#: The claims console. A single static page so the standalone app has a UI with
#: no build step and no Node dependency; it talks to the same /api/insurance
#: routes a caller would.
STATIC_DIR = pathlib.Path(__file__).with_name("static")
UI_FILE = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create tables and seed the sample scenarios on first boot.

    Guarded by ``INSURANCE_SEED_ON_START`` so a deployment can start against an
    existing database without touching its data. ``create_schema`` is
    ``checkfirst``, so it is safe either way.
    """
    create_schema()
    if os.environ.get("INSURANCE_SEED_ON_START", "1") == "1":
        bootstrap(seed=True)
    try:
        yield
    finally:
        dispose_engine()


def create_insurance_app() -> FastAPI:
    """Build the app without serving it, for tests and OpenAPI generation."""
    app = FastAPI(
        title="Life Insurance Claims Decision AI",
        version=__version__,
        description=(
            "Semantic layer + ontology + knowledge graph + deterministic decision "
            "engine for life insurance death claims. Payouts are simulated."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(insurance_router, prefix="/api/insurance", tags=["insurance"])

    # Served on the standalone app only. The platform mount includes the router,
    # not this app, so `/` stays the platform's own frontend there.
    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    def claims_console() -> FileResponse:
        """The claims console: claims, decision, payments, audit, ontology."""
        return FileResponse(UI_FILE, media_type="text/html")

    return app


app = create_insurance_app()
