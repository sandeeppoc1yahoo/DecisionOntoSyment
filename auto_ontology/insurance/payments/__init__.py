# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Simulated payment rail. No real funds move; the controls are real."""

from auto_ontology.insurance.payments.gateway import (
    PaymentRecord,
    SimulatedPaymentGateway,
)

__all__ = ["PaymentRecord", "SimulatedPaymentGateway"]
