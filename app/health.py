"""
app/health.py — GET /health

Never returns 5xx. All failures reflected in status fields.
Computes: db_status, cache_status, per-store feed freshness, uptime_seconds.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select, cast, Date, text

from app.db import AsyncSessionLocal, redis_client
from app.models import Event, HealthResponse, StoreHealth, Store
from app.config import get_settings

# Imported at call time to avoid circular import at module load
import app.main as _main_module

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """
    Liveness + feed freshness endpoint.
    Always returns HTTP 200. Failures are reflected in status fields.
    """
    db_status = "OK"
    cache_status = "OK"
    stores_health: dict[str, StoreHealth] = {}

    # ------------------------------------------------------------------
    # DB check
    # ------------------------------------------------------------------
    try:
        today = datetime.now(timezone.utc).date()

        async with AsyncSessionLocal() as session:
            # All store_ids from stores table
            store_rows = await session.execute(select(Store.store_id))
            known_store_ids: list[str] = [r[0] for r in store_rows.all()]

            # Per-store: last event timestamp + event count today
            stats_stmt = (
                select(
                    Event.store_id,
                    func.max(Event.timestamp).label("last_event_at"),
                    func.count().label("event_count_today"),
                )
                .where(func.date(Event.timestamp) == today)
                .group_by(Event.store_id)
            )
            stats_rows = await session.execute(stats_stmt)
            stats_by_store: dict[str, tuple] = {
                row.store_id: (row.last_event_at, row.event_count_today)
                for row in stats_rows.all()
            }

        stale_cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=settings.stale_feed_minutes
        )

        for store_id in known_store_ids:
            if store_id in stats_by_store:
                last_event_at, count_today = stats_by_store[store_id]
                # Ensure tz-aware for comparison
                if last_event_at.tzinfo is None:
                    last_event_at = last_event_at.replace(tzinfo=timezone.utc)
                feed_status = "STALE_FEED" if last_event_at < stale_cutoff else "OK"
            else:
                # No events today — treat as stale
                last_event_at = datetime.now(timezone.utc) - timedelta(
                    minutes=settings.stale_feed_minutes + 1
                )
                count_today = 0
                feed_status = "STALE_FEED"

            stores_health[store_id] = StoreHealth(
                last_event_at=last_event_at,
                feed_status=feed_status,
                event_count_today=count_today,
            )

    except Exception as exc:
        logger.error("Health DB check failed: %s", exc)
        db_status = "UNAVAILABLE"

    # ------------------------------------------------------------------
    # Redis check
    # ------------------------------------------------------------------
    try:
        if redis_client is None:
            raise RuntimeError("Redis client not initialised")
        await redis_client.ping()
    except Exception as exc:
        logger.error("Health Redis check failed: %s", exc)
        cache_status = "UNAVAILABLE"

    # ------------------------------------------------------------------
    # Uptime
    # ------------------------------------------------------------------
    uptime_seconds = time.monotonic() - (_main_module.APP_START_TIME or time.monotonic())

    # Overall status: OK only if both backing services are up
    overall = "OK" if (db_status == "OK" and cache_status == "OK") else "DEGRADED"

    return HealthResponse(
        status=overall,
        stores=stores_health,
        db_status=db_status,
        cache_status=cache_status,
        uptime_seconds=round(uptime_seconds, 1),
    )