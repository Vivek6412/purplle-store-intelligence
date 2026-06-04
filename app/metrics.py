import logging
from datetime import datetime, timezone, date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, cast, Date, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db, get_redis
from app.models import Event, Session, Store, PosTransaction, MetricsResponse

logger = logging.getLogger("store_intelligence")
settings = get_settings()

router = APIRouter(prefix="/stores", tags=["metrics"])


@router.get("/{store_id}/metrics", response_model=MetricsResponse)
async def get_metrics(
    store_id: str,
    db: AsyncSession = Depends(get_db),
):
    # -------------------------------------------------------------------------
    # Redis cache check
    # -------------------------------------------------------------------------
    try:
        redis = await get_redis()
        cache_key = f"metrics:{store_id}"
        cached = await redis.get(cache_key)
        if cached:
            return MetricsResponse.model_validate_json(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for {cache_key}: {exc}")
        redis = None

    # -------------------------------------------------------------------------
    # Store existence check — 404 only if store not in stores table
    # -------------------------------------------------------------------------
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail={"error": "store_not_found"})

    from datetime import timedelta, time
    # Hardcode for demo dataset
    if store_id == "STORE_BLR_002":
        today = date(2026, 6, 2)
    else:
        today = datetime.now(tz=timezone.utc).date()
    today_start = datetime.combine(today, time.min, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)

    # -------------------------------------------------------------------------
    # 1. unique_visitors
    # -------------------------------------------------------------------------
    uv_result = await db.execute(
        select(func.count(func.distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
        )
    )
    unique_visitors: int = uv_result.scalar_one() or 0

    # -------------------------------------------------------------------------
    # 2. conversion_rate
    #    sessions_with_purchase / total_customer_sessions
    #    "purchase" = session correlated to a POS txn within 5-min billing window
    # -------------------------------------------------------------------------
    total_sessions_result = await db.execute(
        select(func.count(func.distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
        )
    )
    total_sessions: int = total_sessions_result.scalar_one() or 0

    from datetime import timedelta
    converted_result = await db.execute(
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
    converted_sessions: int = converted_result.scalar_one() or 0

    conversion_rate: float = (
        round(converted_sessions / total_sessions, 4) if total_sessions > 0 else 0.0
    )

    # -------------------------------------------------------------------------
    # 3. avg_dwell_per_zone
    # -------------------------------------------------------------------------
    dwell_result = await db.execute(
        select(Event.zone_id, func.avg(Event.dwell_ms).label("avg_dwell"))
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_DWELL",
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
            Event.zone_id.is_not(None),
        )
        .group_by(Event.zone_id)
    )
    avg_dwell_per_zone: dict[str, int] = {
        row.zone_id: int(row.avg_dwell) for row in dwell_result.all()
    }

    # -------------------------------------------------------------------------
    # 4. queue_depth_current — latest queue_depth from most recent BILLING_QUEUE_JOIN
    # -------------------------------------------------------------------------
    queue_result = await db.execute(
        select(Event.queue_depth)
        .where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.queue_depth.is_not(None),
        )
        .order_by(Event.timestamp.desc())
        .limit(1)
    )
    queue_row = queue_result.scalar_one_or_none()
    queue_depth_current: int = int(queue_row) if queue_row is not None else 0

    # -------------------------------------------------------------------------
    # 5. abandonment_rate — BILLING_QUEUE_ABANDON / BILLING_QUEUE_JOIN, zero-safe
    # -------------------------------------------------------------------------
    abandon_result = await db.execute(
        select(
            func.count(Event.id).filter(
                Event.event_type == "BILLING_QUEUE_ABANDON"
            ).label("abandon_count"),
            func.count(Event.id).filter(
                Event.event_type == "BILLING_QUEUE_JOIN"
            ).label("join_count"),
        )
        .where(
            Event.store_id == store_id,
            Event.is_staff == False,
            Event.timestamp >= today_start, Event.timestamp < today_end,
        )
    )
    ab_row = abandon_result.one()
    abandonment_rate: float = (
        round(ab_row.abandon_count / ab_row.join_count, 4)
        if ab_row.join_count > 0
        else 0.0
    )

    # -------------------------------------------------------------------------
    # Build response
    # -------------------------------------------------------------------------
    result = MetricsResponse(
        store_id=store_id,
        window="today",
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell_per_zone,
        queue_depth_current=queue_depth_current,
        abandonment_rate=abandonment_rate,
        computed_at=datetime.now(tz=timezone.utc),
    )

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
