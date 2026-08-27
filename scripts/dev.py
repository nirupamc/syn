"""Development helper: run the Syn gateway locally.

Usage:
    python scripts/dev.py            # runs on 127.0.0.1:8001 (from settings)
    python scripts/dev.py --reload
"""

from __future__ import annotations

import argparse

import uvicorn

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Syn gateway")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()