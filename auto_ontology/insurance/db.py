# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Engine, connection scope and row helpers for the insurance tables.

Deliberately small and dialect-neutral. The platform's own DAL
(`auto_ontology.dal.session`) pins Postgres and a ``search_path``; the insurance
domain must also run on SQLite so the claim scenario is reproducible without a
container. Both use SQLAlchemy Core, so the statements in this package are the
same either way; only the engine construction differs.

Transactions are explicit at the service boundary: one decision writes a
decision row, an approval-history row and an audit event as a single unit, and
none of them should exist without the others.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import RowMapping
from sqlalchemy.pool import StaticPool

from auto_ontology.insurance.config import get_settings
from auto_ontology.insurance.schema import INSURANCE_METADATA

_engine: Engine | None = None
_engine_lock = Lock()


def now_utc() -> datetime:
    """Timezone-aware UTC timestamp, generated in Python for both dialects."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """A readable, collision-resistant id such as ``CLM-9F2A1C3D4E5B``.

    Readable prefixes matter here because ids appear in audit trails and graph
    node names, where a bare uuid tells a reviewer nothing.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _build_engine(url: str, *, echo: bool) -> Engine:
    if url in {"sqlite://", "sqlite:///:memory:"}:
        # One shared connection: an in-memory SQLite database is per-connection,
        # so a pool that opened a second one would see an empty schema.
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=echo,
            future=True,
        )
    elif url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=echo,
            future=True,
        )
    else:
        engine = create_engine(url, pool_pre_ping=True, echo=echo, future=True)
    if engine.dialect.name == "sqlite":
        _enable_sqlite_foreign_keys(engine)
    return engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys unless asked, per connection."""
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def configure_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """(Re)build the process engine.

    Rebuilds rather than mutates: the API tests and the demo both point the
    domain at a fresh database, and reusing a pool across that switch would
    leave half the run reading the previous schema.
    """
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = _build_engine(url or get_settings().db_url, echo=echo)
        return _engine


def get_engine() -> Engine:
    """The process engine, built on first use."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = os.environ.get("INSURANCE_DB_URL") or get_settings().db_url
                _engine = _build_engine(url, echo=False)
    return _engine


def dispose_engine() -> None:
    """Release pooled connections. Idempotent."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None


@contextmanager
def transaction() -> Iterator[Connection]:
    """One unit of work: commits on success, rolls back on any exception."""
    with get_engine().begin() as connection:
        yield connection


@contextmanager
def connection_scope() -> Iterator[Connection]:
    """A read connection with no transaction held beyond the statements."""
    with get_engine().connect() as connection:
        yield connection


def create_schema(engine: Engine | None = None) -> None:
    """Create every domain table that does not already exist."""
    INSURANCE_METADATA.create_all(engine or get_engine(), checkfirst=True)


def drop_schema(engine: Engine | None = None) -> None:
    """Drop every domain table. Used by tests and the demo reset."""
    INSURANCE_METADATA.drop_all(engine or get_engine(), checkfirst=True)


def reset_schema(engine: Engine | None = None) -> None:
    """Drop and recreate. The demo's clean-slate entry point."""
    target = engine or get_engine()
    INSURANCE_METADATA.drop_all(target, checkfirst=True)
    INSURANCE_METADATA.create_all(target, checkfirst=True)


def rows(connection: Connection, statement: Any) -> list[dict[str, Any]]:
    """Materialise a select into plain dicts."""
    return [dict(row) for row in connection.execute(statement).mappings()]


def one(connection: Connection, statement: Any) -> dict[str, Any] | None:
    """First row of a select, or ``None``."""
    result = connection.execute(statement).mappings().first()
    return dict(result) if result is not None else None


def row_dict(row: RowMapping) -> dict[str, Any]:
    """Normalise a single mapping row."""
    return dict(row)
