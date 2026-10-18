"""opt-in question and personal-data erasure

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18 07:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('opt_in_asked_at', app.db.base.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('erased_at', app.db.base.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_column('erased_at')
        batch_op.drop_column('opt_in_asked_at')
