"""M7: telemetry fields on usage_records, indexes for observability.

Revision ID: 0004_m7_observability
Revises: 0003_m6_usage
Create Date: 2026-08-28 05:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_m7_observability"
down_revision = "0003_m6_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Telemetry columns on usage_records (nullable for backward compatibility).
    op.add_column(
        "usage_records",
        sa.Column("backend_latency_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_records",
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_records",
        sa.Column("stream_duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_records",
        sa.Column("total_duration_ms", sa.Integer(), nullable=True),
    )

    # Indexes for observability queries.
    op.create_index(
        "ix_usage_records_status", "usage_records", ["status"]
    )
    op.create_index(
        "ix_usage_records_model", "usage_records", ["model"]
    )
    op.create_index(
        "ix_usage_records_status_started",
        "usage_records",
        ["status", "started_at"],
    )
    op.create_index(
        "ix_usage_records_model_started",
        "usage_records",
        ["model", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_model_started", table_name="usage_records")
    op.drop_index("ix_usage_records_status_started", table_name="usage_records")
    op.drop_index("ix_usage_records_model", table_name="usage_records")
    op.drop_index("ix_usage_records_status", table_name="usage_records")

    op.drop_column("usage_records", "total_duration_ms")
    op.drop_column("usage_records", "stream_duration_ms")
    op.drop_column("usage_records", "ttft_ms")
    op.drop_column("usage_records", "backend_latency_ms")
