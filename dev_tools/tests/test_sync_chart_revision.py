# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""`make migrate-revision` must leave the chart pinned to what it generated.

The rewrite is deliberately line-based: values.yaml is a reviewed file full of
comments, and a YAML round-trip would reflow all of it to change six characters.
"""

from __future__ import annotations

import pathlib

import pytest

from dev_tools.sync_chart_revision import REPO_ROOT, update_pin

VALUES = REPO_ROOT / "helm" / "auto-ontology" / "values.yaml"

SAMPLE = """\
backend:
  # The Alembic head revision this chart's image ships.
  alembicRevision: aaaaaaaaaaaa
  image:
    repository: nvcr.io/nvidia/auto-ontology
"""


def test_it_changes_only_the_revision() -> None:
    updated, previous = update_pin(SAMPLE, "bbbbbbbbbbbb")

    assert previous == "aaaaaaaaaaaa"
    assert "alembicRevision: bbbbbbbbbbbb" in updated
    # Everything around the value, including the comment, is untouched.
    assert updated.replace("bbbbbbbbbbbb", "aaaaaaaaaaaa") == SAMPLE


def test_it_is_a_no_op_when_already_pinned() -> None:
    updated, previous = update_pin(SAMPLE, "aaaaaaaaaaaa")

    assert previous is None
    assert updated == SAMPLE


def test_it_preserves_crlf_line_endings() -> None:
    crlf = SAMPLE.replace("\n", "\r\n")

    updated, previous = update_pin(crlf, "bbbbbbbbbbbb")

    assert previous == "aaaaaaaaaaaa"
    assert "\r\n" in updated
    assert updated.count("\r\n") == crlf.count("\r\n")
    assert "alembicRevision: bbbbbbbbbbbb\r\n" in updated


def test_it_refuses_a_file_without_a_pin() -> None:
    with pytest.raises(SystemExit):
        update_pin("backend:\n  image:\n    tag: 1.0\n", "bbbbbbbbbbbb")


def test_the_committed_chart_has_a_pinnable_line() -> None:
    """The real file still has exactly the line this rewrites."""
    text = VALUES.read_text(encoding="utf-8")
    updated, previous = update_pin(text, "0123456789ab")

    assert previous is not None
    assert updated != text
    assert "alembicRevision: 0123456789ab" in updated
    # One line changed; the file's size changes by the revision's length only.
    assert len(updated.splitlines()) == len(text.splitlines())
    assert pathlib.Path(VALUES).is_file()
