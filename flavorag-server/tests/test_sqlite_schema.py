from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import pytest


@pytest.mark.asyncio
async def test_initialize_sqlite_schema_upgrades_legacy_chunk_table(tmp_path):
    from app.database.sqlite_schema import initialize_sqlite_schema

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE t_knowledge_chunk (
                        id VARCHAR(20) PRIMARY KEY,
                        kb_id VARCHAR(20) NOT NULL,
                        doc_id VARCHAR(20) NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        created_by VARCHAR(20) NOT NULL
                    )
                    """
                )
            )

        added = await initialize_sqlite_schema(engine)

        async with engine.connect() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(t_knowledge_chunk)"))
                ).all()
            }
            asset_table = (
                await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name = 't_knowledge_asset'"
                    )
                )
            ).scalar_one_or_none()

        assert {
            "embedding_content",
            "block_type",
            "page_start",
            "page_end",
            "bbox_json",
            "metadata_json",
        }.issubset(columns)
        assert asset_table == "t_knowledge_asset"
        assert set(added) == {
            "embedding_content",
            "block_type",
            "page_start",
            "page_end",
            "bbox_json",
            "metadata_json",
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_initialize_sqlite_schema_is_idempotent(tmp_path):
    from app.database.sqlite_schema import initialize_sqlite_schema

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        first_added = await initialize_sqlite_schema(engine)
        second_added = await initialize_sqlite_schema(engine)

        assert first_added == []
        assert second_added == []
    finally:
        await engine.dispose()
