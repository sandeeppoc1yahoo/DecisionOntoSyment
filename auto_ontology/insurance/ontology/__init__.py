# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ontology layer: T-Box vocabulary plus deterministic inference.

Public surface:

* ``vocabulary`` - classes, properties, causes, exclusions, constraints.
* ``reasoner`` - classification, subsumption, exclusion matching, validation.
* ``store`` - persistence of the vocabulary into ``insurance_ontology_*``.
"""
