"""M3 tests: database models, persistence, and relationships."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.core import api_keys
from app.models.api_key import ApiKey
from app.models.client import Client
from app.models.client_allowed_model import ClientAllowedModel
from app.models.user import User
from app.services import admin as admin_service


@pytest.fixture
def db(client):
    """Return the app's database, with M3 tables created.

    Depends on the `client` fixture to ensure the lifespan has started and
    the database is wired onto app.state.
    """
    return client.app.state.database


# ---- User creation ----------------------------------------------------------


def test_create_user(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        assert user.id is not None
        assert user.name == "alice"
        assert user.status == "active"
        assert user.created_at is not None
    finally:
        session.close()


def test_create_user_duplicate_raises(db):
    session = db.session_factory()
    try:
        admin_service.create_user(session, "alice")
        from app.core.errors import SynError

        with pytest.raises(SynError) as exc:
            admin_service.create_user(session, "alice")
        assert exc.value.code == "user_exists"
    finally:
        session.close()


def test_create_user_empty_name_raises(db):
    session = db.session_factory()
    try:
        from app.core.errors import ValidationError

        with pytest.raises(ValidationError):
            admin_service.create_user(session, "   ")
    finally:
        session.close()


def test_list_users(db):
    session = db.session_factory()
    try:
        admin_service.create_user(session, "alice")
        admin_service.create_user(session, "bob")
        users = admin_service.list_users(session)
        assert len(users) == 2
        names = {u.name for u in users}
        assert names == {"alice", "bob"}
    finally:
        session.close()


# ---- Client creation --------------------------------------------------------


def test_create_client(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(
            session, user_id=user.id, name="huginn"
        )
        assert client.id is not None
        assert client.user_id == user.id
        assert client.name == "huginn"
        assert client.status == "active"
        # No allowed models by default
        allowed = admin_service.get_client_allowed_models(session, client.id)
        assert allowed == []
    finally:
        session.close()


def test_create_client_with_allowed_models(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(
            session,
            user_id=user.id,
            name="huginn",
            allowed_models=["model-a", "model-b"],
        )
        allowed = admin_service.get_client_allowed_models(session, client.id)
        assert sorted(allowed) == ["model-a", "model-b"]
    finally:
        session.close()


def test_create_client_user_not_found_raises(db):
    session = db.session_factory()
    try:
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            admin_service.create_client(
                session, user_id="nonexistent-id", name="huginn"
            )
    finally:
        session.close()


def test_list_clients_filtered_by_user(db):
    session = db.session_factory()
    try:
        alice = admin_service.create_user(session, "alice")
        bob = admin_service.create_user(session, "bob")
        admin_service.create_client(session, user_id=alice.id, name="c1")
        admin_service.create_client(session, user_id=bob.id, name="c2")

        alice_clients = admin_service.list_clients(session, user_id=alice.id)
        assert len(alice_clients) == 1
        assert alice_clients[0].name == "c1"
    finally:
        session.close()


# ---- API key persistence ----------------------------------------------------


def test_create_api_key_persists_hash_not_secret(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(session, user_id=user.id, name="c")
        api_key, full_token = admin_service.create_api_key(
            session, client_id=client.id, name="k"
        )

        # The full token is NOT in the DB
        assert full_token not in (api_key.key_hash, api_key.key_prefix)
        # The hash is stored
        assert api_key.key_hash == api_keys.hash_api_key(full_token)
        # The prefix is a short visible part
        assert api_key.key_prefix == full_token[:17]
        assert api_key.name == "k"
        assert api_key.revoked_at is None
        assert api_key.last_used_at is None
    finally:
        session.close()


def test_create_api_key_unique(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(session, user_id=user.id, name="c")
        k1, t1 = admin_service.create_api_key(
            session, client_id=client.id, name="k1"
        )
        k2, t2 = admin_service.create_api_key(
            session, client_id=client.id, name="k2"
        )
        assert t1 != t2
        assert k1.key_hash != k2.key_hash
    finally:
        session.close()


def test_list_api_keys(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(session, user_id=user.id, name="c")
        admin_service.create_api_key(session, client_id=client.id, name="k1")
        admin_service.create_api_key(session, client_id=client.id, name="k2")

        keys = admin_service.list_api_keys(session)
        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"k1", "k2"}
    finally:
        session.close()


# ---- Revocation -------------------------------------------------------------


def test_revoke_api_key_marks_revoked_at(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(session, user_id=user.id, name="c")
        api_key, _ = admin_service.create_api_key(
            session, client_id=client.id, name="k"
        )
        assert api_key.revoked_at is None

        revoked = admin_service.revoke_api_key(session, api_key.id)
        assert revoked.revoked_at is not None
        assert isinstance(revoked.revoked_at, _dt.datetime)
    finally:
        session.close()


def test_revoke_api_key_idempotent(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(session, user_id=user.id, name="c")
        api_key, _ = admin_service.create_api_key(
            session, client_id=client.id, name="k"
        )
        first = admin_service.revoke_api_key(session, api_key.id)
        second = admin_service.revoke_api_key(session, api_key.id)
        assert first.revoked_at == second.revoked_at
    finally:
        session.close()


# ---- Rotation ---------------------------------------------------------------


def test_rotate_api_key_creates_new_and_revokes_old(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(session, user_id=user.id, name="c")
        old, _ = admin_service.create_api_key(
            session, client_id=client.id, name="k"
        )
        new_key, new_token, old_key = admin_service.rotate_api_key(
            session, old.id, revoke_old=True
        )
        assert new_key.id != old.id
        assert new_key.client_id == old.client_id
        assert new_token != old.key_hash  # new token is different
        assert old_key.revoked_at is not None
    finally:
        session.close()


def test_rotate_api_key_without_revoke(db):
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "alice")
        client = admin_service.create_client(session, user_id=user.id, name="c")
        old, _ = admin_service.create_api_key(
            session, client_id=client.id, name="k"
        )
        new_key, _, old_key = admin_service.rotate_api_key(
            session, old.id, revoke_old=False
        )
        assert new_key.id != old.id
        assert old_key is None  # not revoked
        # The old key should still be active
        session.refresh(old)
        assert old.revoked_at is None
    finally:
        session.close()
