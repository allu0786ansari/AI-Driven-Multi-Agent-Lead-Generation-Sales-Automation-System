"""meta events (lead stages reported to Meta)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22 10:40:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('meta_events',
    sa.Column('event_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('lead_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('event_name', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', app.db.base.UTCDateTime(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(), nullable=False),
    sa.Column('sent_at', app.db.base.UTCDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.lead_id'], name=op.f('fk_meta_events_lead_id_leads')),
    sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name=op.f('fk_meta_events_user_id_users')),
    sa.PrimaryKeyConstraint('event_id', name=op.f('pk_meta_events')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_meta_events_idempotency_key'))
    )
    with op.batch_alter_table('meta_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_meta_events_status_next_attempt_at'), ['status', 'next_attempt_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_meta_events_lead_id'), ['lead_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('meta_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_meta_events_lead_id'))
        batch_op.drop_index(batch_op.f('ix_meta_events_status_next_attempt_at'))
    op.drop_table('meta_events')
