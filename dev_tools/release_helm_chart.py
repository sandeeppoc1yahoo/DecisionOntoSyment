# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Package ``helm/auto-ontology`` and publish it to the NGC Helm chart registry.

Publishes ``<org>[/<team>]/<name>:<version>``, served from
``https://helm.ngc.nvidia.com/<org>[/<team>]/charts/``.

The chart is staged in nvstaging, but it is written for where it ends up. NGC
Catalog publishing copies the chart from nvstaging to nvidia byte for byte
and rewrites nothing in it, so the images ``values.yaml`` names must already
be their published paths (``--image-registry``, default nvcr.io/nvidia),
not the nvstaging ones release-docker.yml pushes to -- a customer cannot pull
from nvstaging. NeMo-Retriever's chart does the same. Before packaging, this
checks that ``values.yaml`` names exactly ``<image-registry>/<image>:
<app-version>``, so a release can't ship a chart that installs another build
or a registry customers can't reach.

It also compares ``backend.alembicRevision`` with the newest migration under
``alembic/versions``. The backend's ``wait-for-catalog-schema`` init container
blocks until ``public.alembic_version`` reports that value, so a chart pinned
behind its own image would install backend Pods that never start. The check
reads the migration modules rather than asking Alembic: this job installs only
the ``release`` dependency group, and pulling in the backend's closure to read
a dozen two-line modules is not worth it. The authoritative check, which does
ask Alembic, is ``auto_ontology/dal/tests/test_migration_revision.py``.

Usage (from the repo root)::

    helm lint helm/auto-ontology
    python dev_tools/release_helm_chart.py \\
        --org nvstaging --team gsf --version 1.0.0 --app-version 1.0 [--dry-run]

Installing the staged chart before it is published needs the nvstaging images
set explicitly (``--set backend.image.repository=nvcr.io/nvstaging/gsf/...``
and likewise for ingestion and frontend).

Requires ``pip install ngcsdk pyyaml``; ``NGC_CLI_API_KEY`` is needed to publish.
Run by ``.github/workflows/release-helm.yml``.
"""

from __future__ import annotations

import argparse
import ast
import os
import pathlib
import shutil
import subprocess
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
# Where the images live once the catalog publishing MR copies them there.
PUBLISHED_IMAGE_REGISTRY = "nvcr.io/nvidia"
# The migrations the chart's `backend.alembicRevision` has to match.
MIGRATIONS_DIR = REPO_ROOT / "alembic" / "versions"

# values.yaml key -> the image release-docker.yml publishes for it.
CHART_IMAGES = {
    "backend": "auto-ontology",
    "ingestion": "auto-ontology",
    "frontend": "auto-ontology-frontend",
}

_NOT_FOUND_EXC = frozenset({"ResourceNotFoundException", "ChartNotFoundException"})
_ALREADY_EXISTS_EXC = frozenset(
    {"ResourceAlreadyExistsException", "ChartAlreadyExistsException"}
)


def _exc_name(exc: BaseException) -> str:
    return type(exc).__name__


def _namespace(args: argparse.Namespace) -> str:
    return f"{args.org}/{args.team}" if args.team else args.org


def _check_images(values: dict, registry: str, app_version: str) -> list[str]:
    errors = []
    for key, image in CHART_IMAGES.items():
        got = values.get(key, {}).get("image", {})
        want_repo = f"{registry}/{image}"
        if got.get("repository") != want_repo:
            errors.append(
                f"{key}.image.repository is {got.get('repository')!r}, "
                f"expected {want_repo!r}"
            )
        # Compared uncoerced: an unquoted `tag: 1.0` is the float 1.0, which
        # Helm renders as `:1` -- a tag that was never pushed.
        if got.get("tag") != app_version:
            errors.append(
                f"{key}.image.tag is {got.get('tag')!r}, expected {app_version!r}"
                " (quoted)"
            )
    return errors


def _module_constants(module: ast.Module) -> dict[str, object]:
    """The top-level names a migration module sets to a literal."""
    constants: dict[str, object] = {}
    for node in module.body:
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or node.value is None:
                continue
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name, value = node.targets[0].id, node.value
        else:
            continue
        try:
            constants[name] = ast.literal_eval(value)
        except (ValueError, TypeError):
            # A computed revision is not something `upgrade` can follow either;
            # leave it out rather than guess at its value.
            continue
    return constants


def _parents(value: object) -> list[str] | None:
    """``down_revision`` as a list of parents, or ``None`` if it is not one."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def _migration_heads(
    versions_dir: pathlib.Path = MIGRATIONS_DIR,
) -> tuple[set[str], list[str]]:
    """Head revisions, plus anything that could not be read.

    Parsed from the modules' AST rather than their text so a tuple
    ``down_revision`` (a merge revision) is read as two parents, the way Alembic
    reads it. Branch labels are not used in this repository; a migration graph
    that starts using them would be a reason to move this check to Alembic, not
    to extend the parser.
    """
    revisions: dict[str, list[str]] = {}
    parents: set[str] = set()
    problems: list[str] = []
    for path in sorted(versions_dir.glob("*.py")):
        try:
            module = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"{path.name}: cannot be parsed ({exc.msg})")
            continue
        constants = _module_constants(module)
        revision = constants.get("revision")
        if not isinstance(revision, str):
            problems.append(f"{path.name}: has no literal `revision`")
            continue
        if "down_revision" not in constants:
            problems.append(f"{path.name}: has no `down_revision`")
            continue
        down = _parents(constants["down_revision"])
        if down is None:
            problems.append(f"{path.name}: `down_revision` is not a revision id")
            continue
        revisions[revision] = down
        parents.update(down)
    return set(revisions) - parents, problems


def _check_alembic_revision(
    values: dict, versions_dir: pathlib.Path = MIGRATIONS_DIR
) -> list[str]:
    """Errors that would leave the chart waiting on a revision that never lands."""
    pinned = values.get("backend", {}).get("alembicRevision")
    heads, errors = _migration_heads(versions_dir)
    if pinned is None:
        errors.append("backend.alembicRevision is missing from values.yaml")
    if not heads:
        errors.append(f"no migrations found under {versions_dir}")
    elif len(heads) > 1:
        errors.append(
            f"the migration graph has {len(heads)} heads, {sorted(heads)}; "
            "`alembic upgrade head` cannot run until they are merged"
        )
    elif pinned is not None:
        (head,) = heads
        if pinned != head:
            errors.append(
                f"backend.alembicRevision is {pinned!r}, but the newest migration "
                f"is {head!r}; bump the chart in the same commit as the migration"
            )
    return errors


def _stage(chart_dir: pathlib.Path, name: str, version: str, app_version: str) -> None:
    staged = DIST_DIR / name
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(chart_dir, staged)
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
    )
    (staged / ".gitsha").write_text(sha)

    chart_yaml = staged / "Chart.yaml"
    chart = yaml.safe_load(chart_yaml.read_text())
    chart["name"] = name
    chart["version"] = version
    chart["appVersion"] = app_version
    chart_yaml.write_text(yaml.safe_dump(chart, sort_keys=False))


def _publish(args: argparse.Namespace, tgz: pathlib.Path) -> None:
    api_key = os.environ.get("NGC_CLI_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: NGC_CLI_API_KEY environment variable is not set")

    from ngcsdk import Client

    clt = Client()
    clt.configure(api_key=api_key, org_name=args.org, team_name=args.team or None)

    target = f"{_namespace(args)}/{args.name}"
    metadata_kwargs = dict(
        short_description=args.description,
        display_name=args.display_name,
        publisher="NVIDIA",
    )
    # The NGC page's overview tab; the chart has no README of its own yet.
    overview = pathlib.Path(args.chart_dir) / "README.md"
    if overview.is_file():
        metadata_kwargs["overview_filepath"] = str(overview)

    print(f"Updating chart metadata for {target} ...")
    try:
        clt.registry.chart.update(target=target, **metadata_kwargs)
    except Exception as exc:
        if _exc_name(exc) not in _NOT_FOUND_EXC:
            raise
        print(f"Chart '{target}' not found ({_exc_name(exc)}); creating it ...")
        clt.registry.chart.create(target=target, **metadata_kwargs)

    print(f"Pushing chart {target}:{args.version} ...")
    try:
        clt.registry.chart.push(
            target=f"{target}:{args.version}", source_dir=str(tgz.parent)
        )
    except Exception as exc:
        if _exc_name(exc) not in _ALREADY_EXISTS_EXC:
            raise
        # Failed rather than skipped (as Retriever does): a run that keeps the
        # old contents and reports success reads as a release that happened.
        sys.exit(
            f"ERROR: chart version {args.version!r} already exists in NGC "
            f"({_exc_name(exc)}); release it under a new version"
        )
    print(f"Successfully pushed {target}:{args.version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--org", required=True, help="Target NGC org")
    parser.add_argument(
        "-t", "--team", default="", help="Target NGC team; omit for org level"
    )
    parser.add_argument("-v", "--version", required=True, help="Chart version")
    parser.add_argument(
        "-a", "--app-version", required=True, help="Image tag the chart deploys"
    )
    parser.add_argument("-n", "--name", default="auto-ontology", help="Chart name")
    parser.add_argument(
        "--chart-dir", default=str(REPO_ROOT / "helm" / "auto-ontology")
    )
    parser.add_argument("--display-name", default="NVIDIA Auto Ontology Helm Chart")
    parser.add_argument(
        "-d",
        "--description",
        default="Auto Ontology: FastAPI backend, ingestion worker, Next.js "
        "frontend and Postgres (pgvector)",
    )
    parser.add_argument(
        "--image-registry",
        default=PUBLISHED_IMAGE_REGISTRY,
        help="Where values.yaml must say the images are: their published path",
    )
    parser.add_argument("-r", "--dry-run", action="store_true")
    args = parser.parse_args()

    chart_dir = pathlib.Path(args.chart_dir)
    if not chart_dir.is_dir():
        sys.exit(f"ERROR: chart directory does not exist: {chart_dir}")

    values = yaml.safe_load((chart_dir / "values.yaml").read_text())
    errors = _check_images(values, args.image_registry, args.app_version)
    errors += _check_alembic_revision(values)
    if errors:
        print("ERROR: values.yaml does not match this release:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    _stage(chart_dir, args.name, args.version, args.app_version)
    subprocess.check_call(
        ["helm", "package", str(DIST_DIR / args.name), "--destination", str(DIST_DIR)]
    )
    tgz = DIST_DIR / f"{args.name}-{args.version}.tgz"
    if not tgz.is_file():
        sys.exit(f"ERROR: helm package did not produce {tgz}")

    if args.dry_run:
        print(f"[DRY RUN] Chart packaged: {tgz}")
        print(
            f"[DRY RUN] Skipping NGC push of {_namespace(args)}/{args.name}:{args.version}"
        )
        return
    _publish(args, tgz)


if __name__ == "__main__":
    main()
