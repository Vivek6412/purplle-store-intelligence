import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db, get_redis
from app.models import Event, Session, Store, HeatmapZone, HeatmapResponse

logger = logging.getLogger("store_intelligence")
settings = get_settings()

router = APIRouter(prefix="/stores", tags=["heatmap"])


def _load_all_zone_ids(store: Store) -> list[str]:
    """Extract all zone_ids from the store's layout_json."""
    try:
        layout = store.layout_json
        zones = layout.get("zones", [])
        return [z["zone_id"] for z in zones if "zone_id" in z]
    except Exception:
        return []


@router.get("/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    store_id: str,
    db: AsyncSession = Depends(get_db),
):
    # -------------------------------------------------------------------------
    # Redis cache check
    # -------------------------------------------------------------------------
    cache_key = f"heatmap:{store_id}"
    try:
        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return HeatmapResponse.model_validate_json(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for {cache_key}: {exc}")
        redis = None

    # -------------------------------------------------------------------------
    # Store existence check
    # -------------------------------------------------------------------------
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail={"error": "store_not_found"})

    today = datetime.now(tz=timezone.utc).date()
    all_zone_ids = _load_all_zone_ids(store)

    # -------------------------------------------------------------------------
    # Query: visit_count (DISTINCT visitor_id per zone) + avg_dwell_ms
    # -------------------------------------------------------------------------
    zone_result = await db.execute(
        select(
            Event.zone_id,
            func.count(func.distinct(Event.visitor_id)).label("visit_count"),
            func.coalesce(
                func.avg(Event.dwell_ms).filter(Event.event_type == "ZONE_DWELL"),
                0,
            ).label("avg_dwell_ms"),
        )
        .where(
            Event.store_id == store_id,
            Event.is_staff == False,
            Event.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
            cast(Event.timestamp, Date) == today,
            Event.zone_id.is_not(None),
        )
        .group_by(Event.zone_id)
    )
    zone_rows = zone_result.all()

    # Build dict from DB results
    db_zone_data: dict[str, dict] = {
        row.zone_id: {
            "visit_count": row.visit_count,
            "avg_dwell_ms": int(row.avg_dwell_ms),
        }
        for row in zone_rows
    }

    # -------------------------------------------------------------------------
    # Merge with all_zone_ids so zones with 0 visits are included
    # -------------------------------------------------------------------------
    # Build final set: union of DB zones + layout zones
    all_zones_seen = set(all_zone_ids) | set(db_zone_data.keys())
    # Remove None/empty
    all_zones_seen.discard(None)
    all_zones_seen.discard("")

    zone_visit_counts: dict[str, int] = {
        z: db_zone_data.get(z, {}).get("visit_count", 0) for z in all_zones_seen
    }

    # -------------------------------------------------------------------------
    # Normalize visit_count → score 0–100
    # -------------------------------------------------------------------------
    max_v = max(zone_visit_counts.values(), default=0)

    zones: list[HeatmapZone] = []
    for zone_id in sorted(all_zones_seen):
        visit_count = zone_visit_counts[zone_id]
        avg_dwell_ms = db_zone_data.get(zone_id, {}).get("avg_dwell_ms", 0)
        score = int((visit_count / max_v) * 100) if max_v > 0 else 0
        zones.append(
            HeatmapZone(
                zone_id=zone_id,
                visit_count=visit_count,
                avg_dwell_ms=avg_dwell_ms,
                score=score,
            )
        )

    # -------------------------------------------------------------------------
    # Total sessions today (for data_confidence)
    # -------------------------------------------------------------------------
    session_count_result = await db.execute(
        select(func.count(Session.id))
        .where(
            Session.store_id == store_id,
            cast(Session.entry_time, Date) == today,
        )
    )
    window_sessions: int = session_count_result.scalar_one() or 0
    data_confidence = "LOW" if window_sessions < 20 else "HIGH"

    result = HeatmapResponse(
        store_id=store_id,
        zones=zones,
        data_confidence=data_confidence,
        window_sessions=window_sessions,
    )

    # Cache result
    try:
        if redis:
            await redis.setex(
                cache_key,
                settings.heatmap_cache_ttl,
                result.model_dump_json(),
            )
    except Exception as exc:
        logger.warning(f"Redis cache write failed for {cache_key}: {exc}")

    return result
