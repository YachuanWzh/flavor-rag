from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import event
from app.config.settings import settings

_db_url = settings.database_url

if _db_url.startswith("sqlite"):
    # WAL mode allows concurrent reads while a write is in progress;
    # busy_timeout makes writers wait instead of failing immediately.
    engine = create_async_engine(
        _db_url,
        echo=False,
        connect_args={
            "timeout": 30,
            "check_same_thread": False,
        },
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
else:
    engine = create_async_engine(_db_url, echo=False, pool_size=20, max_overflow=10)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
