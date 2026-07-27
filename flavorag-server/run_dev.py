"""Dev launcher — reads DATABASE_URL from .env (PostgreSQL by default).

Usage:
    cd flavorag-server
    pip install -e .
    python run_dev.py

Then open http://localhost:9090/docs

Note: the dev stack uses PostgreSQL (see .env) to avoid SQLite's
single-writer "database is locked" errors under concurrent RAG/SSE load.
Start the infra first:
    docker compose -f docker/infra-stack.compose.yaml up -d
If you need a zero-Docker fallback, set DATABASE_URL to a sqlite+aiosqlite
URL in .env (note: SQLite serializes all writes).
"""
from pathlib import Path

from dotenv import load_dotenv

# .env takes priority over system env vars for local dev
load_dotenv(str(Path(__file__).resolve().parent / ".env"), override=True)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=9090,
        reload=True,
    )
