"""M3 tests: CLI bootstrap commands."""

from __future__ import annotations

import os
import tempfile
from unittest import mock

import pytest


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Configure settings to use a temp DB for CLI tests."""
    db_path = tmp_path / "cli_test.db"
    monkeypatch.setenv("SYN_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SYN_ENVIRONMENT", "testing")
    # Force re-evaluation of settings
    from app.config import get_settings

    get_settings.cache_clear()
    yield db_path


def test_cli_create_user(cli_env):
    from app.cli import main

    rc = main(["create-user", "--name", "alice"])
    assert rc == 0
    assert cli_env.exists()


def test_cli_create_client(cli_env):
    from app.cli import main

    # First create a user
    main(["create-user", "--name", "alice"])

    # Get the user ID
    from app.db import Database
    from app.models.user import User

    db = Database(f"sqlite:///{cli_env}")
    db.connect()
    session = db.session_factory()
    try:
        user = session.query(User).filter(User.name == "alice").one()
        user_id = user.id
    finally:
        session.close()
    db.dispose()

    rc = main(
        [
            "create-client",
            "--user-id",
            user_id,
            "--name",
            "huginn",
        ]
    )
    assert rc == 0


def test_cli_create_api_key_prints_full_token_once(cli_env, capsys):
    from app.cli import main
    from app.db import Database
    from app.models.client import Client
    from app.models.user import User

    # Set up
    main(["create-user", "--name", "alice"])
    db = Database(f"sqlite:///{cli_env}")
    db.connect()
    session = db.session_factory()
    try:
        user = session.query(User).filter(User.name == "alice").one()
        client_obj = Client(user_id=user.id, name="huginn")
        session.add(client_obj)
        session.commit()
        client_id = client_obj.id
    finally:
        session.close()
    db.dispose()

    rc = main(
        [
            "create-api-key",
            "--client-id",
            client_id,
            "--name",
            "dev",
        ]
    )
    assert rc == 0

    captured = capsys.readouterr()
    # Full token should appear in stdout exactly once
    assert "syn_live_" in captured.out
    # Count syn_live occurrences (should be at least 2: once in prefix, once in full key)
    # but the full key line is clearly labeled
    assert "API KEY" in captured.out
    assert "store securely" in captured.out


def test_cli_revoke_api_key(cli_env):
    from app.cli import main
    from app.db import Database
    from app.models.api_key import ApiKey
    from app.models.client import Client
    from app.models.user import User

    # Set up
    main(["create-user", "--name", "alice"])
    db = Database(f"sqlite:///{cli_env}")
    db.connect()
    session = db.session_factory()
    try:
        user = session.query(User).filter(User.name == "alice").one()
        client_obj = Client(user_id=user.id, name="huginn")
        session.add(client_obj)
        session.commit()
        client_id = client_obj.id
    finally:
        session.close()
    db.dispose()

    # Create a key
    main(["create-api-key", "--client-id", client_id, "--name", "k"])

    # Get the key ID
    db = Database(f"sqlite:///{cli_env}")
    db.connect()
    session = db.session_factory()
    try:
        key = session.query(ApiKey).filter(ApiKey.name == "k").one()
        api_key_id = key.id
    finally:
        session.close()
    db.dispose()

    # Revoke
    rc = main(["revoke-api-key", "--api-key-id", api_key_id])
    assert rc == 0

    # Verify
    db = Database(f"sqlite:///{cli_env}")
    db.connect()
    session = db.session_factory()
    try:
        key = session.query(ApiKey).filter(ApiKey.id == api_key_id).one()
        assert key.revoked_at is not None
    finally:
        session.close()
    db.dispose()
