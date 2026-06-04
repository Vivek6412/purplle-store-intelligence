"""initial

Revision ID: 001
Revises: 
Create Date: 2026-05-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. stores
    op.create_table('stores',
        sa.Column('store_id', sa.String(length=50), nullable=False),
        sa.Column('layout_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('open_time', sa.Time(), nullable=False),
        sa.Column('close_time', sa.Time(), nullable=False),
        sa.Column('timezone', sa.String(length=50), server_default='Asia/Kolkata', nullable=False),
        sa.PrimaryKeyConstraint('store_id')
    )

    # 2. anomaly_log
    op.create_table('anomaly_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('store_id', sa.String(length=50), nullable=False),
        sa.Column('anomaly_type', sa.String(length=50), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('suggested_action', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_anomaly_store', 'anomaly_log', ['store_id', sa.text('detected_at DESC')], unique=False)

    # 3. pos_transactions
    op.create_table('pos_transactions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('store_id', sa.String(length=50), nullable=False),
        sa.Column('transaction_id', sa.String(length=50), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('basket_value', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('matched_visitor', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('transaction_id')
    )
    op.create_index('idx_pos_store_ts', 'pos_transactions', ['store_id', sa.text('ts DESC')], unique=False)

    # 4. sessions
    op.create_table('sessions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('visitor_id', sa.String(length=20), nullable=False),
        sa.Column('store_id', sa.String(length=50), nullable=False),
        sa.Column('entry_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('exit_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_converted', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
        sa.Column('reentry_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('zone_sequence', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('visitor_id', 'store_id', 'entry_time', name='uq_session_visit')
    )
    op.create_index('idx_sessions_store', 'sessions', ['store_id', sa.text('entry_time DESC')], unique=False)

    # 5. events
    op.create_table('events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('event_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('store_id', sa.String(length=50), nullable=False),
        sa.Column('camera_id', sa.String(length=50), nullable=False),
        sa.Column('visitor_id', sa.String(length=20), nullable=False),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('zone_id', sa.String(length=50), nullable=True),
        sa.Column('dwell_ms', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('is_staff', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('queue_depth', sa.Integer(), nullable=True),
        sa.Column('sku_zone', sa.String(length=100), nullable=True),
        sa.Column('session_seq', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id')
    )
    op.create_index('idx_events_staff', 'events', ['is_staff'], unique=False)
    op.create_index('idx_events_store_ts', 'events', ['store_id', sa.text('timestamp DESC')], unique=False)
    op.create_index('idx_events_type', 'events', ['event_type'], unique=False)
    op.create_index('idx_events_visitor', 'events', ['visitor_id'], unique=False)


def downgrade() -> None:
    # 5. events
    op.drop_index('idx_events_visitor', table_name='events')
    op.drop_index('idx_events_type', table_name='events')
    op.drop_index('idx_events_store_ts', table_name='events')
    op.drop_index('idx_events_staff', table_name='events')
    op.drop_table('events')

    # 4. sessions
    op.drop_index('idx_sessions_store', table_name='sessions')
    op.drop_table('sessions')

    # 3. pos_transactions
    op.drop_index('idx_pos_store_ts', table_name='pos_transactions')
    op.drop_table('pos_transactions')

    # 2. anomaly_log
    op.drop_index('idx_anomaly_store', table_name='anomaly_log')
    op.drop_table('anomaly_log')

    # 1. stores
    op.drop_table('stores')
