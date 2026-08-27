"""Empty initial migration.

M0 establishes the Alembic migration foundation only. No persistent tables are
required in M0, so this migration intentionally changes nothing. Future
milestones (users/API keys/usage) will add real revisions via ``alembic
revision --autogenerate`` against :data:`app.db.base.Base.metadata`.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-27 19:20:00
"""
from __future__ import annotations


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: M0 introduces no persistent tables."""
    pass


def downgrade() -> None:
    """No-op: nothing to revert in M0."""
    pass