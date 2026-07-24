"""Dev launcher — uses SQLite instead of PostgreSQL, zero Docker required.

Usage:
    cd ragent-server
    pip install -e .
    python run_dev.py

Then open http://localhost:9090/docs
"""
import os

# Override to SQLite before importing anything
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///ragent_dev.db"

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=9090,
        reload=True,
    )
