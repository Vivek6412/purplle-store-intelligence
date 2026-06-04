"""
app/ingestion.py — POST /events/ingest

Idempotent by event_id (ON CONFLICT DO NOTHING).
Partial success: per-event errors collected, valid events still ingested.
Session upsert: ENTRY creates, EXIT closes, REENTRY creates + increments prior.
Redis: cache invalidation + pub/sub publish after ingest.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, get_redis
from app.models import (
    Event,
    IngestRequest,
    IngestResponse,
    Session as SessionModel,
)

router = APIRouter(prefix="/events", tags=["events"])
logger = logging.getLogger(__name__)

# Redis cache keys invalidated on ingest
_CACHE_KEY_PREFIXES = ("metrics", "heatmap", "anomalies", "funnel")


# =============================================================================
# Helpers
# =============================================================================

def _event_to_row(ev) -> dict[str, Any]:
    """Flatten EventSchema → dict matching Event ORM columns."""
    return {
        "event_id":    ev.event_id,
        "store_id":    ev.store_id,
        "camera_id":   ev.camera_id,
        "visitor_id":  ev.visitor_id,
        "event_type":  ev.event_type,
        "timestamp":   ev.timestamp,
        "zone_id":     ev.zone_id,
        "dwell_ms":    ev.dwell_ms,
        "is_staff":    ev.is_staff,
        "confidence":  ev.confidence,
        "queue_depth": ev.metadata.queue_depth,
        "sku_zone":    ev.metadata.sku_zone,
        "session_seq": ev.metadata.session_seq,
    }


async def _upsert_session(
    db: AsyncSession,
    ev,
    now: datetime,
) -> None:
    """
    Maintain sessions table based on event_type.
    ENTRY      → INSERT new session (ignore if duplicate entry_time — shouldn't happen)
    REENTRY    → INSERT new session row + increment reentry_count on most recent prior session
    EXIT       → set exit_time on the open session for this visitor+store
    Other      → no-op (zone events don't alter session lifecycle)
    """
    if ev.is_staff:
        return  # Staff sessions stored in events but not in sessions table

    if ev.event_type == "ENTRY":
        stmt = pg_insert(SessionModel).values(
            visitor_id=ev.visitor_id,
            store_id=ev.store_id,
            entry_time=ev.timestamp,
            exit_time=None,
            is_converted=False,
            reentry_count=0,
            zone_sequence=[],
            updated_at=now,
        ).on_conflict_do_nothing(
            index_elements=["visitor_id", "store_id", "entry_time"]
        )
        await db.execute(stmt)

    elif ev.event_type == "REENTRY":
        # 1. Increment reentry_count on the most recent prior session
        prior_stmt = (
            select(SessionModel)
            .where(
                SessionModel.visitor_id == ev.visitor_id,
                SessionModel.store_id == ev.store_id,
            )
            .order_by(SessionModel.entry_time.desc())
            .limit(1)
        )
        prior_row = (await db.execute(prior_stmt)).scalar_one_or_none()

        if prior_row:
            await db.execute(
                update(SessionModel)
                .where(SessionModel.id == prior_row.id)
                .values(
                    reentry_count=SessionModel.reentry_count + 1,
                    updated_at=now,
                )
            )

        # 2. Create new session row for the re-entry visit
        new_stmt = pg_insert(SessionModel).values(
            visitor_id=ev.visitor_id,
            store_id=ev.store_id,
            entry_time=ev.timestamp,
            exit_time=None,
            is_converted=False,
            reentry_count=0,
            zone_sequence=[],
            updated_at=now,
        ).on_conflict_do_nothing(
            index_elements=["visitor_id", "store_id", "entry_time"]
        )
        await db.execute(new_stmt)

    elif ev.event_type == "EXIT":
        # Close the most recent open session for this visitor+store
        open_session_stmt = (
            select(SessionModel)
            .where(
                SessionModel.visitor_id == ev.visitor_id,
                SessionModel.store_id == ev.store_id,
                SessionModel.exit_time.is_(None),
            )
            .order_by(SessionModel.entry_time.desc())
            .limit(1)
        )
        open_session = (await db.execute(open_session_stmt)).scalar_one_or_none()
        if open_session:
            await db.execute(
                update(SessionModel)
                .where(SessionModel.id == open_session.id)
                .values(exit_time=ev.timestamp, updated_at=now)
            )


# =============================================================================
# Route
# =============================================================================

@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(
    payload: IngestRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    trace_id = getattr(request.state, "trace_id", "unknown")

    ingested = 0
    duplicates = 0
    errors: list[dict] = []
    affected_store_ids: set[str] = set()
    published_events: list[tuple[str, str]] = []  # (channel, json_str)

    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Per-event insert — partial success model
    # ------------------------------------------------------------------
    total_db_failures = 0

    for idx, ev in enumerate(payload.events):
        try:
            async with db.begin_nested():  # savepoint per event
                row = _event_to_row(ev)
                stmt = pg_insert(Event).values(**row)
                stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
                result = await db.execute(stmt)

                if result.rowcount == 0:
                    duplicates += 1
                else:
                    ingested += 1
                    affected_store_ids.add(ev.store_id)

                    # Session lifecycle maintenance (only on new inserts)
                    await _upsert_session(db, ev, now)

                    # Queue publish payload (after commit)
                    published_events.append((
                        f"events:{ev.store_id}",
                        ev.model_dump_json(),
                    ))

        except Exception as exc:
            total_db_failures += 1
            logger.error(
                "event_ingest_error idx=%d event_id=%s error=%s",
                idx,
                str(ev.event_id),
                str(exc),
            )
            errors.append({
                "index": idx,
                "event_id": str(ev.event_id),
                "error": str(exc),
            })

    # Total DB failure (every event failed) — likely connection issue
    if total_db_failures > 0 and ingested == 0 and duplicates == 0:
        raise HTTPException(
            status_code=503,
            detail={"error": "database_unavailable", "detail": "All event inserts failed"},
        )

    # Commit the outer transaction
    try:
        await db.commit()
    except Exception as exc:
        logger.error("ingest_commit_failed trace_id=%s error=%s", trace_id, exc)
        raise HTTPException(
            status_code=503,
            detail={"error": "database_unavailable", "detail": str(exc)},
        )

    # ------------------------------------------------------------------
    # Redis: cache invalidation + pub/sub
    # ------------------------------------------------------------------
    try:
        redis = await get_redis()

        # Invalidate all metric caches for affected stores
        cache_keys = [
            f"{prefix}:{store_id}"
            for store_id in affected_store_ids
            for prefix in _CACHE_KEY_PREFIXES
        ]
        if cache_keys:
            await redis.delete(*cache_keys)

        # Publish each new event to its store channel (for SSE dashboard)
        for channel, payload_json in published_events:
            await redis.publish(channel, payload_json)

    except Exception as exc:
        # Redis failure is non-fatal — log and continue
        logger.warning("redis_post_ingest_failed trace_id=%s error=%s", trace_id, exc)

    # ------------------------------------------------------------------
    # Structured log
    # ------------------------------------------------------------------
    logger.info(
        "ingest_complete",
        extra={
            "trace_id": trace_id,
            "store_ids": list(affected_store_ids),
            "ingested": ingested,
            "duplicates": duplicates,
            "errors": len(errors),
        },
    )

    return IngestResponse(
        ingested=ingested,
        duplicates=duplicates,
        errors=errors,
    )