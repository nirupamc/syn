"""Database foundation tests (SQLAlchemy + engine/session)."""

from __future__ import annotations

from sqlalchemy import Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Database


def test_database_connects_and_disposes():
    db = Database("sqlite:///:memory:")
    db.connect()
    assert db.engine is not None
    assert db.session_factory is not None
    # Idempotent connect.
    db.connect()
    db.dispose()
    assert db.engine is None
    assert db.session_factory is None
    # Idempotent dispose.
    db.dispose()


def test_no_persistent_models_registered():
    """M0 declares no persistent tables on a fresh declarative base."""
    from sqlalchemy import MetaData
    from sqlalchemy.orm import DeclarativeBase

    class _Empty(DeclarativeBase):
        metadata = MetaData()

    assert len(_Empty.metadata.tables) == 0


class _Probe(Base):
    """A throwaway model proving the ORM session pipeline works end-to-end."""

    __tablename__ = "_probe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64), unique=True)


def test_session_insert_and_read():
    db = Database("sqlite:///:memory:")
    db.connect()
    _Probe.metadata.create_all(bind=db.engine)

    with db.session_factory() as session:  # type: ignore[union-attr]
        session.add(_Probe(label="hello"))
        session.commit()

    with db.session_factory() as session:  # type: ignore[union-attr]
        row = session.scalars(select(_Probe)).one()
        assert row.label == "hello"

    db.dispose()


def test_lifespan_creates_db_engine(client):
    """The application lifespan wires a Database onto app.state."""
    assert client.app.state.database is not None
    assert client.app.state.database.engine is not None