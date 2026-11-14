# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Translate domain errors into HTTP responses at the route boundary.

A context manager rather than an app-level exception handler, because this
router is mounted both on its own demo app and on the platform's FastAPI app
(`auto_ontology.server.create_app`). An app-level handler registered on one is
not inherited by the other; a `with domain_errors():` block travels with the
route.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from auto_ontology.insurance.errors import InsuranceDomainError


@contextmanager
def domain_errors() -> Iterator[None]:
    try:
        yield
    except InsuranceDomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
