"""Database engine and session management (SQLAlchemy 2.x)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

logger = logging.getLogger("syn.db")


def _redact_dsn(dsn: str) -> str:
    """Return a DSN safe for logging (strips any embedded credentials)."""
    marker = "://"
    if marker not in dsn:
        return dsn
    scheme, _, rest = dsn.partition(marker)
    authority, _, remainder = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[-1]
    return f"{scheme}://{authority}{remainder}"


class Database:
    """Owns the engine and session factory for the configured database.

    In M0 this is a thin, deliberately synchronous wrapper around SQLAlchemy.
    It is also the seam where a future async engine (or PostgreSQL in a later
    milestone) would be introduced without touching API/service code.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: Optional[Engine] = None
        self.session_factory: Optional[sessionmaker[Session]] = None

    def connect(self) -> "Database":
        """Create the engine and a session factory. Idempotent."""
        if self.engine is not None:
            return self
        connect_args: dict[str, object] = {}
        if self.database_url.startswith("sqlite"):
            # SQLite file DBs can be opened from multiple threads.
            connect_args = {"check_same_thread": False}
        self.engine = create_engine(
            self.database_url,
            connect_args=connect_args,
            future=True,
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            future=True,
        )
        logger.info("database engine ready (%s)", _redact_dsn(self.database_url))
        return self

    def dispose(self) -> None:
        """Dispose of the engine resources."""
        if self.engine is not None:
            self.engine.dispose()
            logger.info("database engine disposed")
        self.engine = None
        self.session_factory = None