import asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import redis.asyncio as aioredis
from alembic.config import Config
from alembic import command
from app.config import get_settings

settings = get_settings()

engine_kwargs = {
    "pool_pre_ping": True,
}
if not settings.database_url.startswith("sqlite"):
    engine_kwargs["pool_size"] = 10

engine = create_async_engine(
    settings.database_url,
    **engine_kwargs
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

def _run_alembic_upgrade():
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

async def init_db():
    # Windows thread bug bypass: Use create_all natively on the main event loop
    # instead of spawning a sub-thread for Alembic which hangs ProactorEventLoop.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Module-level Redis client
redis_client: aioredis.Redis | None = None

async def init_redis():
    """Initializes the redis_client. To be called in FastAPI lifespan."""
    global redis_client
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

async def close_redis():
    """Closes the redis_client. To be called in FastAPI lifespan."""
    global redis_client
    if redis_client:
        await redis_client.close()

async def get_redis() -> aioredis.Redis:
    """Dependency for FastAPI to get the Redis client."""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    return redis_client
