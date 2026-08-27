"""SQLAlchemy database foundation.

M0 establishes that the database engine, sessions, and Alembic are wired
correctly. No substantive persistent entities are introduced yet; that happens
in later milestones (users/API keys/usage). The declarative base exists now so
future models and Alembic autogenerate can rely on it.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all Syn ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)