"""M3 tests: Alembic migration up/down."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect


@pytest.fixture
def fresh_db_dir(tmp_path, monkeypatch):
    """Create a temp dir for a fresh test database and set SYN_DATABASE_URL."""
    db_path = tmp_path / "migration_test.db"
    monkeypatch.setenv("SYN_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SYN_ENVIRONMENT", "testing")
    # Force re-evaluation of settings
    from app.config import get_settings

    get_settings.cache_clear()
    return db_path


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    """Run alembic with the project root as cwd."""
    return subprocess.run(
        ["alembic", *args],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )


def test_alembic_upgrade_creates_m3_tables(fresh_db_dir):
    # First upgrade to head (runs both 0001 and 0002)
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed: {result.stderr}"

    # Check tables exist
    engine = create_engine(f"sqlite:///{fresh_db_dir}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    expected = {"users", "clients", "api_keys", "client_allowed_models"}
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_alembic_downgrade_drops_m3_tables(fresh_db_dir):
    # Upgrade first
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0

    # Downgrade to base
    result = _run_alembic("downgrade", "base")
    assert result.returncode == 0, f"alembic downgrade failed: {result.stderr}"

    # Check M3 tables are gone
    engine = create_engine(f"sqlite:///{fresh_db_dir}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    m3_tables = {"users", "clients", "api_keys", "client_allowed_models"}
    assert not (m3_tables & tables), (
        f"M3 tables still present after downgrade: {m3_tables & tables}"
    )


def test_alembic_migration_creates_indexes(fresh_db_dir):
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0

    engine = create_engine(f"sqlite:///{fresh_db_dir}")
    inspector = inspect(engine)

    # Check indexes on api_keys
    api_key_indexes = inspector.get_indexes("api_keys")
    index_names = {ix["name"] for ix in api_key_indexes}
    assert "ix_api_keys_key_hash" in index_names
    assert "ix_api_keys_key_prefix" in index_names
    assert "ix_api_keys_client_id" in index_names


def test_alembic_migration_creates_foreign_keys(fresh_db_dir):
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0

    engine = create_engine(f"sqlite:///{fresh_db_dir}")
    inspector = inspect(engine)

    # Check foreign keys on clients → users
    client_fks = inspector.get_foreign_keys("clients")
    fk_tables = {fk["referred_table"] for fk in client_fks}
    assert "users" in fk_tables

    # Check foreign keys on api_keys → clients
    api_key_fks = inspector.get_foreign_keys("api_keys")
    fk_tables = {fk["referred_table"] for fk in api_key_fks}
    assert "clients" in fk_tables
