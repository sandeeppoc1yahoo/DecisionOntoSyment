# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures: a clean database seeded fresh for every test.

The environment is set at import time, before anything imports the API app, so
the app's lifespan and the fixtures agree on one database. A file-backed SQLite
database would leak decisions between tests and make ordering matter.

The same suite runs against Postgres by setting ``INSURANCE_TEST_DB_URL``::

    INSURANCE_TEST_DB_URL=postgresql+psycopg://... \
        uv run pytest auto_ontology/insurance/tests -q

That is how the dialect portability of the schema, the money/percentage types
and the audit hash chain are verified rather than assumed. The database named
there is dropped and recreated per test, so point it at a disposable instance.
"""

from __future__ import annotations

import os

#: Default to in-memory SQLite; override for a Postgres run.
TEST_DB_URL = os.environ.get("INSURANCE_TEST_DB_URL", "sqlite://")

os.environ["INSURANCE_DB_URL"] = TEST_DB_URL
os.environ["INSURANCE_SEED_ON_START"] = "0"

import pytest  # noqa: E402

from auto_ontology.insurance.bootstrap import bootstrap  # noqa: E402
from auto_ontology.insurance.db import (  # noqa: E402
    configure_engine,
    dispose_engine,
    reset_schema,
)


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    configure_engine(TEST_DB_URL)
    yield
    dispose_engine()


@pytest.fixture()
def fresh_db() -> None:
    """A clean schema with both sample scenarios and the catalogs."""
    reset_schema()
    bootstrap(seed=True)
