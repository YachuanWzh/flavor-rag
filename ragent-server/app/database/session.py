from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.config.settings import settings

_db_url = settings.database_url

if _db_url.startswith("sqlite"):
    engine = create_async_engine(_db_url, echo=False)
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
