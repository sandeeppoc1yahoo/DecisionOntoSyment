# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hash-chained audit trail.

Every adjudication and every payment writes an ``insurance_audit_event``. Each
event stores the previous event's hash (for the same correlation id) and its own
hash over a canonical serialisation of the event, so removing or editing a row
breaks the chain at that point and every later event in the correlation.

That is not blockchain theatre. A claims file must survive the question "prove
nobody edited this after the money moved", and a chain answers it with a single
recomputation. It is deliberately simple enough to re-implement in a spreadsheet
during a dispute.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, select

from auto_ontology.insurance.db import new_id, now_utc, one
from auto_ontology.insurance.schema import insurance_audit_event


def canonical_json(payload: Any) -> str:
    """Stable serialisation: sorted keys, no whitespace, stringified decimals."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def canonical_timestamp(value: datetime) -> str:
    """One spelling of a timestamp for hashing, whatever the dialect returns.

    SQLite drops the offset on round-trip, so hashing ``isoformat()`` of a
    timezone-aware value at write time and of a naive value at verify time
    produces two different strings for the same instant, and a perfectly intact
    chain reports as broken. Normalising to naive UTC in both places makes the
    hash depend on the moment, not on the driver.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def sha256_hex(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def record_event(
    connection: Connection,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Append one event, chaining it to the last event of this correlation."""
    previous = one(
        connection,
        select(insurance_audit_event)
        .where(insurance_audit_event.c.correlation_id == correlation_id)
        .order_by(insurance_audit_event.c.occurred_at.desc())
        .limit(1),
    )
    previous_hash = previous["event_hash"] if previous else None
    occurred_at = now_utc()
    if previous is not None:
        # Two events in the same microsecond would make "the previous event"
        # ambiguous, and the chain is only verifiable if its order is total.
        floor = previous["occurred_at"]
        if floor.tzinfo is None:
            floor = floor.replace(tzinfo=UTC)
        floor = floor + timedelta(microseconds=1)
        if occurred_at <= floor:
            occurred_at = floor
    body = {
        "occurred_at": canonical_timestamp(occurred_at),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "correlation_id": correlation_id,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    event_hash = sha256_hex(body)
    event_id = new_id("AUD")
    connection.execute(
        insurance_audit_event.insert().values(
            id=event_id,
            occurred_at=occurred_at,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload=payload,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )
    )
    return {"id": event_id, "event_hash": event_hash, "occurred_at": occurred_at}


def verify_chain(connection: Connection, correlation_id: str) -> dict[str, Any]:
    """Recompute the chain for one correlation id.

    Returns the events checked, and the first broken link if any. Used by the
    API's audit endpoint and by the payment verification step.
    """
    from auto_ontology.insurance.db import rows

    events = rows(
        connection,
        select(insurance_audit_event)
        .where(insurance_audit_event.c.correlation_id == correlation_id)
        .order_by(insurance_audit_event.c.occurred_at),
    )
    previous_hash: str | None = None
    for event in events:
        body = {
            "occurred_at": canonical_timestamp(event["occurred_at"]),
            "actor_type": event["actor_type"],
            "actor_id": event["actor_id"],
            "action": event["action"],
            "entity_type": event["entity_type"],
            "entity_id": event["entity_id"],
            "correlation_id": event["correlation_id"],
            "payload": event["payload"],
            "previous_hash": previous_hash,
        }
        if sha256_hex(body) != event["event_hash"]:
            return {
                "correlation_id": correlation_id,
                "events_checked": len(events),
                "intact": False,
                "broken_at": event["id"],
            }
        previous_hash = event["event_hash"]
    return {
        "correlation_id": correlation_id,
        "events_checked": len(events),
        "intact": True,
        "broken_at": None,
    }
