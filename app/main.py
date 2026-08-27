"""FastAPI application entry point for Syn."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import health_router
from app.config import Settings, get_settings
from app.core.errors import NotFoundError, SynError
from app.core.request_id import RequestIDMiddleware, get_request_id
from app.db import Database
from app.logging import RequestIDFilter, get_logger

logger = get_logger("syn.main")


def _create_tables(settings: Settings, db: Database) -> None:
    """Create tables from metadata.

    In M0 the schema is empty (no entities). This is used purely so the
    database foundation can be proven; real schema management belongs to
    Alembic migrations (see ``alembic/``). We prefer migrations, so this is
    guarded and tolerant of an empty schema.
    """
    # Import so declarative models (none yet in M0) are registered.
    import app.models  # noqa: F401  (registers models on Base.metadata)

    from app.db.base import Base

    Base.metadata.create_all(bind=db.engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    request_id_filter = RequestIDFilter()
    logger.info("%s starting (environment=%s)", settings.app_name, settings.environment.value)

    db = Database(settings.database_url)
    db.connect()
    if settings.environment.value in {"development", "testing"}:
        _create_tables(settings, db)
    app.state.database = db

    logger.info("%s ready on %s:%s", settings.app_name, settings.host, settings.port)
    try:
        yield
    finally:
        logger.info("%s shutting down", settings.app_name)
        db.dispose()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the FastAPI application for the provided (or default) settings."""
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Syn - a self-hosted LLM inference gateway / control plane. "
            "M0: architecture & service foundation."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Request/correlation ID foundation.
    app.add_middleware(RequestIDMiddleware)

    # Central error handler translating SynError into the internal API model.
    @app.exception_handler(SynError)
    async def syn_error_handler(request: Request, exc: SynError) -> JSONResponse:
        payload = exc.to_dict()
        payload.setdefault("request_id", get_request_id())
        logger.warning("syn error %s on %s %s", exc.code, request.method, request.url.path)
        return JSONResponse(status_code=exc.http_status, content=payload)

    # Fallback for unknown not-found (FastAPI's default 404 body is fine).
    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=exc.to_dict())

    # Include routers.
    app.include_router(health_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.app_name.lower(), "version": settings.app_version}

    return app


app = create_app()