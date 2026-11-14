# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Portable column types for money and percentages.

Both are stored as integers (cents, basis points) and returned as ``Decimal``.
That is not premature precision: "net payout" and "allocation sums to 100%" are
the two numbers a claims decision is allowed to be wrong about zero times, and a
float column makes both of them merely approximately true.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy.types import TypeDecorator


class Money(TypeDecorator):
    """Money stored as integer cents, returned as a two-place ``Decimal``."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> int | None:
        if value is None:
            return None
        cents = (Decimal(str(value)) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return int(cents)

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


class Percent(TypeDecorator):
    """A percentage stored as basis points (100% -> 10000)."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> int | None:
        if value is None:
            return None
        points = (Decimal(str(value)) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return int(points)

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def as_money(value: Decimal | int | float | str | None) -> Decimal:
    """Round a money value to cents, half-up."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
