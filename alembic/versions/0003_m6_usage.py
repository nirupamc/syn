"""M6: usage records, client/key policy columns.

Revision ID: 0003_m6_usage
Revises: 0002_m3_auth
Create Date: 2026-08-28 04:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_m6_usage"
down_revision = "0002_m3_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Client policy columns (nullable; None means "inherit from system default")
    op.add_column(
        "clients",
        sa.Column("requests_per_minute", sa.Integer(), nullable=True),
    )
    op.add_column(
        "clients",
        sa.Column("requests_per_day", sa.Integer(), nullable=True),
    )
    op.add_column(
        "clients",
        sa.Column("tokens_per_day", sa.Integer(), nullable=True),
    )

    # ApiKey policy columns (nullable; None means "inherit from client")
    op.add_column(
        "api_keys",
        sa.Column("requests_per_minute", sa.Integer(), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("requests_per_day", sa.Integer(), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("tokens_per_day", sa.Integer(), nullable=True),
    )

    # Usage records table
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "client_id",
            sa.String(length=36),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "api_key_id",
            sa.String(length=36),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("streaming", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("queue_wait_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_usage_records_request_id", "usage_records", ["request_id"]
    )
    op.create_index(
        "ix_usage_records_api_key_id", "usage_records", ["api_key_id"]
    )
    op.create_index(
        "ix_usage_records_client_id", "usage_records", ["client_id"]
    )
    op.create_index(
        "ix_usage_records_started_at", "usage_records", ["started_at"]
    )
    op.create_index(
        "ix_usage_records_client_started",
        "usage_records",
        ["client_id", "started_at"],
    )
    op.create_index(
        "ix_usage_records_api_key_started",
        "usage_records",
        ["api_key_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_api_key_started", table_name="usage_records")
    op.drop_index("ix_usage_records_client_started", table_name="usage_records")
    op.drop_index("ix_usage_records_started_at", table_name="usage_records")
    op.drop_index("ix_usage_records_client_id", table_name="usage_records")
    op.drop_index("ix_usage_records_api_key_id", table_name="usage_records")
    op.drop_index("ix_usage_records_request_id", table_name="usage_records")
    op.drop_table("usage_records")

    op.drop_column("api_keys", "tokens_per_day")
    op.drop_column("api_keys", "requests_per_day")
    op.drop_column("api_keys", "requests_per_minute")

    op.drop_column("clients", "tokens_per_day")
    op.drop_column("clients", "requests_per_day")
    op.drop_column("clients", "requests_per_minute")
