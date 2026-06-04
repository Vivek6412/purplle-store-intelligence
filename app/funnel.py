import logging
from datetime import datetime, timezone, date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, cast, Date, exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db, get_redis
from app.models import Event, Session, Store, FunnelStage, FunnelResponse

logger = logging.getLogger("store_intelligence")
settings = get_settings()

router = APIRouter(prefix="/stores", tags=["funnel"])


def _drop_off_pct(prev: int, curr: int) -> float:
    """Compute drop-off percentage, zero-safe."""
    return round(((prev - curr) / prev) * 100, 1) if prev > 0 else 0.0


@router.get("/{store_id}/funnel", response_model=FunnelResponse)
async def get_funnel(
    store_id: str,
    db: AsyncSession = Depends(get_db),
):
    # -------------------------------------------------------------------------
    # Redis cache check
    # -------------------------------------------------------------------------
    cache_key = f"funnel:{store_id}"
    try:
        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return FunnelResponse.model_validate_json(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for {cache_key}: {exc}")
        redis = None

    # -------------------------------------------------------------------------
    # Store existence check
    # -------------------------------------------------------------------------
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail={"error": "store_not_found"})

    from datetime import timedelta, time
    today = datetime.now(tz=timezone.utc).date()
    today_start = datetime.combine(today, time.min, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)

    # -------------------------------------------------------------------------
    # Stage 1 — ENTRY: COUNT DISTINCT visitor_id in sessions table
    # WHERE is_staff=False AND date=today (deduplicated — re-entries count as 1 unique visitor)
    # -------------------------------------------------------------------------
    entry_result = await db.execute(
        select(func.count(func.distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
        )
    )
    entry_count: int = entry_result.scalar_one() or 0

    # -------------------------------------------------------------------------
    # Stage 2 — ZONE_VISIT: sessions with ≥1 ZONE_ENTER (not BILLING)
    # -------------------------------------------------------------------------
    zone_visit_result = await db.execute(
        select(func.count(func.distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_ENTER",
            Event.zone_id != "BILLING",
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
        )
    )
    zone_visit_count: int = zone_visit_result.scalar_one() or 0

    # -------------------------------------------------------------------------
    # Stage 3 — BILLING_QUEUE: sessions with ≥1 BILLING_QUEUE_JOIN
    # -------------------------------------------------------------------------
    billing_result = await db.execute(
        select(func.count(func.distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            (Event.zone_id == "BILLING") | (Event.event_type == "BILLING_QUEUE_JOIN"),
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
        )
    )
    billing_count: int = billing_result.scalar_one() or 0

    # -------------------------------------------------------------------------
    # Stage 4 — PURCHASE: sessions with is_converted=True
    # -------------------------------------------------------------------------
    from datetime import timedelta
    purchase_result = await db.execute(
        select(func.count(func.distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            (Event.zone_id == "BILLING") | (Event.event_type == "BILLING_QUEUE_JOIN"),
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
        )
        .join(
            PosTransaction,
            (PosTransaction.store_id == Event.store_id)
            & (PosTransaction.ts >= Event.timestamp)
            & (PosTransaction.ts <= Event.timestamp + timedelta(minutes=5))
        )
    )
    purchase_count: int = purchase_result.scalar_one() or 0

    # -------------------------------------------------------------------------
    # Build stages with drop-off %
    # -------------------------------------------------------------------------
    stages = [
        FunnelStage(
            stage="ENTRY",
            count=entry_count,
            drop_off_pct=0.0,
        ),
        FunnelStage(
            stage="ZONE_VISIT",
            count=zone_visit_count,
            drop_off_pct=_drop_off_pct(entry_count, zone_visit_count),
        ),
        FunnelStage(
            stage="BILLING_QUEUE",
            count=billing_count,
            drop_off_pct=_drop_off_pct(zone_visit_count, billing_count),
        ),
        FunnelStage(
            stage="PURCHASE",
            count=purchase_count,
            drop_off_pct=_drop_off_pct(billing_count, purchase_count),
        ),
    ]

    result = FunnelResponse(store_id=store_id, stages=stages)

    # Cache result
    try:
        if redis:
            await redis.setex(
                cache_key,
                settings.metrics_cache_ttl,
                result.model_dump_json(),
            )
    except Exception as exc:
        logger.warning(f"Redis cache write failed for {cache_key}: {exc}")

    return result
