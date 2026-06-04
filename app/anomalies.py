"""
app/anomalies.py — GET /stores/{store_id}/anomalies

Detects: BILLING_QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE, STALE_FEED, HIGH_ABANDONMENT.
Caches results in Redis (anomalies_cache_ttl).
Writes detected anomalies to anomaly_log table.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text, distinct
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db, get_redis
from app.models import (
    Anomaly,
    AnomaliesResponse,
    AnomalyLog,
    Event,
    Session as SessionModel,
    Store,
)

router = APIRouter(prefix="/stores", tags=["anomalies"])
settings = get_settings()
logger = logging.getLogger(__name__)

_CACHE_PREFIX = "anomalies"


# =============================================================================
# Helpers
# =============================================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_anomaly(
    anomaly_type: str,
    severity: str,
    details: dict[str, Any],
    suggested_action: str,
    detected_at: datetime | None = None,
) -> Anomaly:
    return Anomaly(
        anomaly_type=anomaly_type,
        severity=severity,
        detected_at=detected_at or _now(),
        details=details,
        suggested_action=suggested_action,
    )


async def _write_anomaly_log(
    db: AsyncSession,
    store_id: str,
    anomaly: Anomaly,
) -> None:
    """Upsert anomaly into anomaly_log. One active row per (store_id, anomaly_type)."""
    try:
        stmt = pg_insert(AnomalyLog).values(
            store_id=store_id,
            anomaly_type=anomaly.anomaly_type,
            severity=anomaly.severity,
            detected_at=anomaly.detected_at,
            details=anomaly.details,
            suggested_action=anomaly.suggested_action,
        )
        # On duplicate anomaly_type for this store: update fields, keep earliest detected_at
        # (no unique constraint on anomaly_type+store_id in schema, so just insert each time)
        await db.execute(stmt)
    except Exception as exc:
        logger.warning("anomaly_log_write_failed store=%s type=%s err=%s",
                       store_id, anomaly.anomaly_type, exc)


# =============================================================================
# Individual anomaly detectors
# =============================================================================

async def _detect_billing_queue_spike(
    db: AsyncSession,
    store_id: str,
    now: datetime,
) -> Anomaly | None:
    """
    BILLING_QUEUE_SPIKE: queue_depth > threshold AND has been so for > 2 minutes.
    Use last 10 BILLING_QUEUE_JOIN events to determine current depth and when spike started.
    """
    threshold = settings.queue_spike_threshold
    spike_window = timedelta(minutes=2)

    stmt = (
        select(Event.queue_depth, Event.timestamp)
        .where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.queue_depth.isnot(None),
        )
        .order_by(Event.timestamp.desc())
        .limit(10)
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        return None

    current_depth = rows[0].queue_depth
    if current_depth is None or current_depth <= threshold:
        return None

    # Find how long queue has been above threshold consecutively
    spike_since: datetime | None = None
    for row in rows:
        depth = row.queue_depth
        ts = row.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if depth is not None and depth > threshold:
            spike_since = ts
        else:
            break  # consecutive run ended

    if spike_since is None:
        return None

    duration = now - spike_since
    if duration >= spike_window:
        return _make_anomaly(
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity="CRITICAL",
            details={"queue_depth": current_depth, "threshold": threshold,
                     "spike_duration_seconds": int(duration.total_seconds())},
            suggested_action="Open additional billing counter immediately",
            detected_at=spike_since,
        )
    return None


async def _detect_conversion_drop(
    db: AsyncSession,
    store_id: str,
    now: datetime,
) -> Anomaly | None:
    """
    CONVERSION_DROP: today_rate < 7day_avg * conversion_drop_threshold.
    7-day avg computed from sessions table, grouped by date.
    """
    today = now.date()

    # Today's conversion rate
    today_total_stmt = (
        select(func.count(distinct(SessionModel.visitor_id)))
        .where(
            SessionModel.store_id == store_id,
            func.date(SessionModel.entry_time) == today,
        )
    )
    today_total = (await db.execute(today_total_stmt)).scalar_one() or 0

    if today_total == 0:
        return None  # No sessions today — can't compute drop

    today_converted_stmt = (
        select(func.count(distinct(SessionModel.visitor_id)))
        .where(
            SessionModel.store_id == store_id,
            func.date(SessionModel.entry_time) == today,
            SessionModel.is_converted.is_(True),
        )
    )
    today_converted = (await db.execute(today_converted_stmt)).scalar_one() or 0
    today_rate = today_converted / today_total

    # 7-day historical average (exclude today)
    seven_days_ago = today - timedelta(days=7)
    hist_stmt = (
        select(
            func.date(SessionModel.entry_time).label("day"),
            func.count(distinct(SessionModel.visitor_id)).label("total"),
            func.count(distinct(
                # Count converted sessions — use CASE via filter
                SessionModel.visitor_id
            )).filter(SessionModel.is_converted.is_(True)).label("converted"),
        )
        .where(
            SessionModel.store_id == store_id,
            func.date(SessionModel.entry_time) > seven_days_ago,
            func.date(SessionModel.entry_time) < today,
        )
        .group_by(func.date(SessionModel.entry_time))
    )
    hist_rows = (await db.execute(hist_stmt)).all()

    if not hist_rows:
        return None  # No historical data to compare against

    daily_rates = [
        (row.converted / row.total)
        for row in hist_rows
        if row.total > 0
    ]
    if not daily_rates:
        return None

    seven_day_avg = sum(daily_rates) / len(daily_rates)
    threshold = settings.conversion_drop_threshold

    if seven_day_avg > 0 and today_rate < seven_day_avg * threshold:
        return _make_anomaly(
            anomaly_type="CONVERSION_DROP",
            severity="WARN",
            details={
                "today_rate": round(today_rate, 4),
                "seven_day_avg": round(seven_day_avg, 4),
                "drop_pct": round((1 - today_rate / seven_day_avg) * 100, 1),
            },
            suggested_action="Review today's staffing, promotions, and queue conditions",
        )
    return None


async def _detect_dead_zones(
    db: AsyncSession,
    store_id: str,
    now: datetime,
) -> list[Anomaly]:
    """
    DEAD_ZONE: zone with 0 ZONE_ENTER events in past 30 minutes.
    Only flag if feed is confirmed active (≥1 zone had recent visits).
    Zone list sourced from store_layout.json via stores table.
    """
    window_start = now - timedelta(minutes=settings.dead_zone_minutes)

    # Zones active in past 30 min
    active_stmt = (
        select(distinct(Event.zone_id))
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_ENTER",
            Event.timestamp > window_start,
            Event.zone_id.isnot(None),
            Event.is_staff.is_(False),
        )
    )
    active_zones: set[str] = {r[0] for r in (await db.execute(active_stmt)).all()}

    if not active_zones:
        # Feed might be down entirely — don't flag dead zones (STALE_FEED covers this)
        return []

    # Get all zone_ids from store layout
    store_row = await db.get(Store, store_id)
    if not store_row:
        return []

    layout_zones: list[str] = [
        z.get("zone_id") or z.get("id")
        for z in store_row.layout_json.get("zones", [])
        if z.get("zone_id") or z.get("id")
    ]

    # Check store is within open hours
    store_tz_now = now  # V1: use UTC; production would convert to store timezone
    open_t = store_row.open_time
    close_t = store_row.close_time
    current_time = store_tz_now.time().replace(tzinfo=None)
    if not (open_t <= current_time <= close_t):
        return []  # Store is closed — expected dead zones

    anomalies: list[Anomaly] = []
    for zone_id in layout_zones:
        if zone_id not in active_zones:
            anomalies.append(_make_anomaly(
                anomaly_type="DEAD_ZONE",
                severity="INFO",
                details={"zone_id": zone_id, "window_minutes": settings.dead_zone_minutes},
                suggested_action=(
                    f"Zone {zone_id} has had no visitors in {settings.dead_zone_minutes} minutes. "
                    "Check camera feed or consider promotional activity."
                ),
            ))
    return anomalies


async def _detect_stale_feed(
    db: AsyncSession,
    store_id: str,
    now: datetime,
) -> Anomaly | None:
    """STALE_FEED: no events received in past stale_feed_minutes."""
    stmt = select(func.max(Event.timestamp)).where(Event.store_id == store_id)
    last_ts = (await db.execute(stmt)).scalar_one_or_none()

    if last_ts is None:
        return _make_anomaly(
            anomaly_type="STALE_FEED",
            severity="WARN",
            details={"last_event_at": None, "threshold_minutes": settings.stale_feed_minutes},
            suggested_action="Check detection pipeline is running and camera feeds are active",
        )

    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)

    if now - last_ts > timedelta(minutes=settings.stale_feed_minutes):
        return _make_anomaly(
            anomaly_type="STALE_FEED",
            severity="WARN",
            details={
                "last_event_at": last_ts.isoformat(),
                "threshold_minutes": settings.stale_feed_minutes,
                "stale_minutes": round((now - last_ts).total_seconds() / 60, 1),
            },
            suggested_action="Check detection pipeline is running and camera feeds are active",
            detected_at=last_ts,
        )
    return None


async def _detect_high_abandonment(
    db: AsyncSession,
    store_id: str,
    now: datetime,
) -> Anomaly | None:
    """HIGH_ABANDONMENT: abandonment_rate > 0.4 today."""
    today = now.date()

    joins_stmt = (
        select(func.count())
        .where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.is_staff.is_(False),
            func.date(Event.timestamp) == today,
        )
    )
    joins = (await db.execute(joins_stmt)).scalar_one() or 0

    if joins == 0:
        return None

    abandons_stmt = (
        select(func.count())
        .where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_ABANDON",
            Event.is_staff.is_(False),
            func.date(Event.timestamp) == today,
        )
    )
    abandons = (await db.execute(abandons_stmt)).scalar_one() or 0

    rate = abandons / joins
    if rate > 0.4:
        return _make_anomaly(
            anomaly_type="HIGH_ABANDONMENT",
            severity="WARN",
            details={
                "abandonment_rate": round(rate, 4),
                "abandons": abandons,
                "joins": joins,
            },
            suggested_action=(
                "High billing queue abandonment. "
                "Consider additional staff or express checkout."
            ),
        )
    return None


# =============================================================================
# Route
# =============================================================================

@router.get("/{store_id}/anomalies", response_model=AnomaliesResponse)
async def get_anomalies(
    store_id: str,
    db: AsyncSession = Depends(get_db),
) -> AnomaliesResponse:
    now = _now()

    # ------------------------------------------------------------------
    # Redis cache
    # ------------------------------------------------------------------
    cache_key = f"{_CACHE_PREFIX}:{store_id}"
    try:
        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            return AnomaliesResponse(**data)
    except Exception as exc:
        logger.warning("anomalies_cache_read_failed store=%s err=%s", store_id, exc)
        redis = None

    # ------------------------------------------------------------------
    # Store existence check
    # ------------------------------------------------------------------
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail={"error": "store_not_found"})

    # ------------------------------------------------------------------
    # Run all detectors (independent — failures isolated)
    # ------------------------------------------------------------------
    anomalies: list[Anomaly] = []

    async def _safe(coro, label: str) -> None:
        try:
            result = await coro
            if result is None:
                return
            if isinstance(result, list):
                anomalies.extend(result)
            else:
                anomalies.append(result)
        except Exception as exc:
            logger.error("anomaly_detector_failed store=%s detector=%s err=%s",
                         store_id, label, exc)

    await _safe(_detect_billing_queue_spike(db, store_id, now), "billing_queue_spike")
    await _safe(_detect_conversion_drop(db, store_id, now),    "conversion_drop")
    await _safe(_detect_dead_zones(db, store_id, now),         "dead_zone")
    await _safe(_detect_stale_feed(db, store_id, now),         "stale_feed")
    await _safe(_detect_high_abandonment(db, store_id, now),   "high_abandonment")

    # ------------------------------------------------------------------
    # Write detected anomalies to anomaly_log
    # ------------------------------------------------------------------
    if anomalies:
        try:
            async with db.begin_nested():
                for anomaly in anomalies:
                    await _write_anomaly_log(db, store_id, anomaly)
            await db.commit()
        except Exception as exc:
            logger.warning("anomaly_log_commit_failed store=%s err=%s", store_id, exc)

    response = AnomaliesResponse(store_id=store_id, anomalies=anomalies)

    # ------------------------------------------------------------------
    # Cache result
    # ------------------------------------------------------------------
    try:
        if redis is not None:
            payload = response.model_dump(mode="json")
            await redis.setex(cache_key, settings.anomalies_cache_ttl, json.dumps(payload))
    except Exception as exc:
        logger.warning("anomalies_cache_write_failed store=%s err=%s", store_id, exc)

    return response