# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Helm chart's pinned Alembic revision must match the head migration.

The backend's ``wait-for-catalog-schema`` init container blocks until
``public.alembic_version`` equals ``backend.alembicRevision``. That makes the value
load-bearing in a way a comment cannot enforce: pin it to a revision that never
arrives and every backend Pod waits forever; leave it behind after adding a
migration and the gate passes before the new schema is applied, which is the
bug it exists to prevent.
"""

from __future__ import annotations

import functools
import pathlib
import re

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = pathlib.Path(__file__).resolve().parents[3]
VERSIONS = ROOT / "alembic" / "versions"
VALUES = ROOT / "helm" / "auto-ontology" / "values.yaml"


@functools.lru_cache(maxsize=1)
def _scripts() -> ScriptDirectory:
    """Alembic's own view of the migration graph.

    Parsed by Alembic rather than by regex over the version files: only Alembic
    knows how merge revisions, tuples and branch labels combine, and the value
    that matters is the one ``upgrade head`` acts on, not one this test can
    reconstruct from the source text.
    """
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(VERSIONS.parent))
    return ScriptDirectory.from_config(config)


def _head_revisions() -> list[str]:
    """The revisions Alembic reports as heads."""
    return list(_scripts().get_heads())


def test_there_is_exactly_one_head_migration() -> None:
    """Two heads mean `upgrade head` is ambiguous and the gate cannot be pinned."""
    heads = _head_revisions()
    assert len(heads) == 1, f"expected a single head, found {heads}"


def test_every_migration_is_reachable_from_the_head() -> None:
    """`upgrade head` must walk the whole graph, not just a single head.

    A revision parented onto an older ancestor leaves two heads and
    ``alembic upgrade head`` refuses to run at all; a disconnected revision can
    also leave tables missing while the head still looks unique. Walking
    base -> head and comparing against every known revision catches both, so a
    migration that exists on disk but would never be applied fails here rather
    than as missing columns after a deploy.
    """
    scripts = _scripts()
    heads = scripts.get_heads()
    assert len(heads) == 1, f"expected a single head, found {heads}"
    on_path = {
        revision.revision for revision in scripts.iterate_revisions(heads[0], "base")
    }
    every = {revision.revision for revision in scripts.walk_revisions()}
    assert on_path == every, (
        f"revisions unreachable from head {heads[0]}: {sorted(every - on_path)}"
    )


@pytest.mark.skipif(not VALUES.exists(), reason="helm chart not present")
def test_the_chart_pins_the_head_revision() -> None:
    head = _head_revisions()[0]
    match = re.search(r"^\s*alembicRevision:\s*(\S+)", VALUES.read_text(), re.M)
    assert match, "backend.alembicRevision is missing from values.yaml"
    assert match.group(1) == head, (
        f"values.yaml pins {match.group(1)} but the head migration is {head}. "
        "Bump it in the same commit as the migration, or every backend Pod "
        "will block on a revision that never arrives."
    )
