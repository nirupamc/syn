"""CLI bootstrap commands (M3).

The system needs a way to create the very first user/client/api-key without
requiring a key. These CLI commands run against the configured database
directly (NOT over HTTP) and are the documented bootstrap path.

Usage examples::

    python -m app.cli create-user --name alice
    python -m app.cli create-client --user-id <id> --name huginn
    python -m app.cli create-api-key --client-id <id> --name dev-key
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from app.config import get_settings
from app.core.errors import SynError
from app.db import Database
from app.logging import get_logger
from app.services import admin as admin_service

logger = get_logger("syn.cli")


def _init_db() -> Database:
    settings = get_settings()
    db = Database(settings.database_url)
    db.connect()
    # Ensure M3 tables exist (for fresh DBs).
    import app.models  # noqa: F401
    from app.db.base import Base

    Base.metadata.create_all(bind=db.engine)
    return db


def _cmd_create_user(args: argparse.Namespace) -> int:
    db = _init_db()
    try:
        session = db.session_factory()
        try:
            user = admin_service.create_user(session, args.name)
            print(f"Created user:")
            print(f"  id    = {user.id}")
            print(f"  name  = {user.name}")
            print(f"  status= {user.status}")
            return 0
        finally:
            session.close()
    except SynError as e:
        print(f"error: {e.detail} ({e.code})", file=sys.stderr)
        return 1
    finally:
        db.dispose()


def _cmd_create_client(args: argparse.Namespace) -> int:
    db = _init_db()
    try:
        session = db.session_factory()
        try:
            allowed = args.allowed_model if args.allowed_model else None
            client = admin_service.create_client(
                session,
                user_id=args.user_id,
                name=args.name,
                description=args.description,
                allowed_models=allowed,
            )
            print(f"Created client:")
            print(f"  id              = {client.id}")
            print(f"  user_id         = {client.user_id}")
            print(f"  name            = {client.name}")
            if client.description:
                print(f"  description     = {client.description}")
            if allowed:
                print(f"  allowed_models  = {allowed}")
            return 0
        finally:
            session.close()
    except SynError as e:
        print(f"error: {e.detail} ({e.code})", file=sys.stderr)
        return 1
    finally:
        db.dispose()


def _cmd_create_api_key(args: argparse.Namespace) -> int:
    db = _init_db()
    try:
        session = db.session_factory()
        try:
            api_key, full_token = admin_service.create_api_key(
                session,
                client_id=args.client_id,
                name=args.name,
            )
            print(f"Created API key:")
            print(f"  id        = {api_key.id}")
            print(f"  name      = {api_key.name}")
            print(f"  prefix    = {api_key.key_prefix}")
            print(f"  client_id = {api_key.client_id}")
            print()
            print("API KEY (shown once — store securely now):")
            print(f"  {full_token}")
            return 0
        finally:
            session.close()
    except SynError as e:
        print(f"error: {e.detail} ({e.code})", file=sys.stderr)
        return 1
    finally:
        db.dispose()


def _cmd_revoke_api_key(args: argparse.Namespace) -> int:
    db = _init_db()
    try:
        session = db.session_factory()
        try:
            api_key = admin_service.revoke_api_key(session, args.api_key_id)
            print(f"Revoked API key:")
            print(f"  id          = {api_key.id}")
            print(f"  prefix      = {api_key.key_prefix}")
            print(f"  revoked_at  = {api_key.revoked_at.isoformat() if api_key.revoked_at else 'n/a'}")
            return 0
        finally:
            session.close()
    except SynError as e:
        print(f"error: {e.detail} ({e.code})", file=sys.stderr)
        return 1
    finally:
        db.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Syn CLI bootstrap commands (M3).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_user = sub.add_parser("create-user", help="Create a new user")
    p_user.add_argument("--name", required=True)
    p_user.set_defaults(func=_cmd_create_user)

    p_client = sub.add_parser("create-client", help="Create a new client")
    p_client.add_argument("--user-id", required=True)
    p_client.add_argument("--name", required=True)
    p_client.add_argument("--description", default=None)
    p_client.add_argument(
        "--allowed-model",
        action="append",
        default=None,
        help="Restrict client to a model ID (repeatable)",
    )
    p_client.set_defaults(func=_cmd_create_client)

    p_key = sub.add_parser("create-api-key", help="Create a new API key")
    p_key.add_argument("--client-id", required=True)
    p_key.add_argument("--name", required=True)
    p_key.set_defaults(func=_cmd_create_api_key)

    p_revoke = sub.add_parser("revoke-api-key", help="Revoke an API key")
    p_revoke.add_argument("--api-key-id", required=True)
    p_revoke.set_defaults(func=_cmd_revoke_api_key)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
