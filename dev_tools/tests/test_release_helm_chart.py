# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The chart's pinned Alembic revision must be the one its image ships.

``backend.alembicRevision`` is what the backend's ``wait-for-catalog-schema``
init container waits for, so a chart pinned behind the migrations in the same
tag installs backend Pods that never become ready. ``release_helm_chart.py``
refuses to package such a chart; these tests cover that refusal and the
dependency-free head detection it relies on.
"""

from __future__ import annotations

import pathlib

import yaml

from dev_tools.release_helm_chart import _check_alembic_revision, _migration_heads

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUES = ROOT / "helm" / "auto-ontology" / "values.yaml"


def _migration(tmp_path: pathlib.Path, name: str, revision: str, down: str) -> None:
    (tmp_path / name).write_text(
        f'revision: str = "{revision}"\ndown_revision: Union[str, None] = "{down}"\n'
    )


def test_a_linear_chain_has_one_head(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.py").write_text('revision: str = "aaa"\ndown_revision = None\n')
    _migration(tmp_path, "b.py", "bbb", "aaa")

    heads, problems = _migration_heads(tmp_path)
    assert (heads, problems) == ({"bbb"}, [])


def test_a_merge_revision_has_two_parents(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.py").write_text('revision: str = "aaa"\ndown_revision = None\n')
    (tmp_path / "b.py").write_text('revision: str = "bbb"\ndown_revision = "aaa"\n')
    (tmp_path / "c.py").write_text('revision: str = "ccc"\ndown_revision = "aaa"\n')
    (tmp_path / "m.py").write_text(
        'revision: str = "mmm"\ndown_revision = ("bbb", "ccc")\n'
    )

    heads, problems = _migration_heads(tmp_path)
    assert (heads, problems) == ({"mmm"}, [])


def test_a_branched_graph_is_two_heads(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.py").write_text('revision: str = "aaa"\ndown_revision = None\n')
    _migration(tmp_path, "b.py", "bbb", "aaa")
    _migration(tmp_path, "c.py", "ccc", "aaa")

    errors = _check_alembic_revision({"backend": {"alembicRevision": "bbb"}}, tmp_path)
    assert len(errors) == 1
    assert "2 heads" in errors[0]
    assert "ccc" in errors[0]


def test_a_stale_pin_is_refused(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.py").write_text('revision: str = "aaa"\ndown_revision = None\n')
    _migration(tmp_path, "b.py", "bbb", "aaa")

    errors = _check_alembic_revision({"backend": {"alembicRevision": "aaa"}}, tmp_path)
    assert len(errors) == 1
    assert "'aaa'" in errors[0] and "'bbb'" in errors[0]


def test_a_missing_pin_is_refused(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.py").write_text('revision: str = "aaa"\ndown_revision = None\n')

    errors = _check_alembic_revision({"backend": {}}, tmp_path)
    assert any("missing from values.yaml" in error for error in errors)


def test_an_unreadable_migration_is_refused(tmp_path: pathlib.Path) -> None:
    (tmp_path / "broken.py").write_text("revision = \n")

    heads, problems = _migration_heads(tmp_path)
    assert heads == set()
    assert len(problems) == 1 and problems[0].startswith("broken.py")


def test_the_committed_chart_pins_the_current_head() -> None:
    """The chart in this commit would package, not be refused."""
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    assert _check_alembic_revision(values) == []
