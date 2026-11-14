# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Serve the standalone claims API."""

from __future__ import annotations

import os

import uvicorn

from auto_ontology.insurance.api.app import app


def main() -> None:
    uvicorn.run(
        app,
        host=os.environ.get("INSURANCE_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("INSURANCE_API_PORT", "3100")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
