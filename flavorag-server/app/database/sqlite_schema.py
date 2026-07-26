"""Development SQLite schema initialization and additive compatibility upgrades.

Production databases are versioned with Alembic. SQLite is also supported as a
zero-setup development database, where ``metadata.create_all`` creates missing
tables but cannot add columns to tables that already exist. Keep the small,
explicit compatibility upgrades here so an existing development database can
start safely after an additive model change.
"""

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import Base


_KNOWLEDGE_CHUNK_COLUMNS: dict[str, str] = {
    "embedding_content": "TEXT",
    "block_type": "VARCHAR(32)",
    "page_start": "INTEGER",
    "page_end": "INTEGER",
    "bbox_json": "JSON",
    "metadata_json": "JSON",
}


async def initialize_sqlite_schema(engine: AsyncEngine) -> list[str]:
    """Create SQLite tables and add known missing columns.

    Returns the chunk column names added during this invocation. The SQL is
    deliberately static and the existing schema is inspected first, making
    repeated application idempotent.
    """
    if engine.url.get_backend_name() != "sqlite":
        raise ValueError("initialize_sqlite_schema requires a SQLite engine")

    added_columns: list[str] = []
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        existing_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("t_knowledge_chunk")
            }
        )

        for column_name, column_type in _KNOWLEDGE_CHUNK_COLUMNS.items():
            if column_name in existing_columns:
                continue
            await conn.execute(
                text(
                    f"ALTER TABLE t_knowledge_chunk "
                    f"ADD COLUMN {column_name} {column_type}"
                )
            )
            added_columns.append(column_name)

    return added_columns
