"""M9: backend_id on usage_records for multi-backend attribution.

Revision ID: 0005_m9_routing
Revises: 0004_m7_observability
Create Date: 2026-08-29 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_m9_routing"
down_revision = "0004_m7_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # backend_id: nullable for backward compatibility with M0-M8 rows.
    op.add_column(
        "usage_records",
        sa.Column("backend_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_usage_records_backend_id", "usage_records", ["backend_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_backend_id", table_name="usage_records")
    op.drop_column("usage_records", "backend_id")
