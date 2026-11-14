# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""add life insurance claims domain

Revision ID: c4a7f1b9d2e6
Revises: caee572fa81c
Create Date: 2026-11-14 09:00:00.000000

The 23 domain tables are defined once, in
``auto_ontology/insurance/schema.py``, and this migration creates exactly that
metadata. Writing 23 ``op.create_table`` blocks by hand would duplicate the
schema — the failure mode the platform's own ``schema.py`` docstring warns
about: a column added to the model and forgotten in the migration is invisible
until deploy.

``INSURANCE_METADATA`` is also registered in ``alembic/env.py`` as a second
target, so ``alembic check`` compares these tables against the same definition
this migration applies.
"""

from typing import Sequence, Union

from alembic import op

from auto_ontology.insurance.schema import INSURANCE_METADATA

# revision identifiers, used by Alembic.
revision: str = "c4a7f1b9d2e6"
down_revision: Union[str, Sequence[str], None] = "caee572fa81c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the insurance domain tables in the platform's default schema."""
    INSURANCE_METADATA.create_all(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    """Drop them, children first."""
    INSURANCE_METADATA.drop_all(op.get_bind(), checkfirst=False)
