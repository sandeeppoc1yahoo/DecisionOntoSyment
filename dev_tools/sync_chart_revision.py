# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Point the Helm chart at the newest Alembic migration.

``backend.alembicRevision`` is what the backend's ``wait-for-catalog-schema``
init container waits for, so it has to be bumped in the same commit as any
migration. ``make migrate-revision`` runs this after
``alembic revision --autogenerate``; run it by hand if you created the revision
another way.

Only the one line is rewritten. ``values.yaml`` carries comments, ordering and
line endings that a YAML round-trip would not preserve, and a release that
reformatted the chart would be a review of noise.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSIONS_DIR = REPO_ROOT / "alembic" / "versions"
VALUES = REPO_ROOT / "helm" / "auto-ontology" / "values.yaml"

# Indentation and spacing are captured so only the value changes. The lookahead
# keeps the match off the newline, which leaves CRLF files with their CRLF.
_PIN = re.compile(
    r"^(?P<prefix>[ \t]*alembicRevision:[ \t]*)(?P<value>[^\s#]+)(?=[ \t]*(?:\r?\n|$))",
    re.M,
)


def head_revision(versions_dir: pathlib.Path = VERSIONS_DIR) -> str:
    """The single revision ``alembic upgrade head`` would apply."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(versions_dir.parent))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        # Two heads mean `upgrade head` cannot run, so there is no revision to
        # pin; let the operator merge first rather than pick one arbitrarily.
        sys.exit(f"ERROR: expected a single migration head, found {heads or 'none'}")
    return heads[0]


def update_pin(text: str, revision: str) -> tuple[str, str | None]:
    """Return ``text`` with the pin set to ``revision``, and the old value.

    The old value is ``None`` when the file already pins ``revision``, so the
    caller can leave the file untouched instead of rewriting identical bytes.
    """
    match = _PIN.search(text)
    if match is None:
        sys.exit(f"ERROR: no `alembicRevision:` line found in {VALUES}")
    previous = match.group("value")
    if previous == revision:
        return text, None
    replaced = (
        text[: match.start()] + match.group("prefix") + revision + text[match.end() :]
    )
    return replaced, previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--values", default=str(VALUES), help="Chart values file")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report the change without writing"
    )
    args = parser.parse_args()

    values_path = pathlib.Path(args.values)
    revision = head_revision()
    with values_path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()

    updated, previous = update_pin(text, revision)
    if previous is None:
        print(f"backend.alembicRevision already pins {revision}")
        return
    print(f"backend.alembicRevision: {previous} -> {revision}")
    if args.dry_run:
        return
    with values_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(updated)


if __name__ == "__main__":
    main()
