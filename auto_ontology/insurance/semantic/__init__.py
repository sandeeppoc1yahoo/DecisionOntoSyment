# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Semantic layer: certified definitions and their single implementation.

``definitions`` is the catalog (what each number means); ``engine`` computes it.
Anything downstream - decision rules, payment amounts, the API's explanation -
reads these values rather than recomputing them.
"""
