"""
app/main.py — FastAPI entrypoint.
Lifespan: migrations, Redis, store seed, POS load.
Middleware: structured per-request logging.
Global handler: no raw stack traces.
SSE: Redis pub/sub → EventSource stream.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from app.config import get_settings
from app.db import (
    AsyncSessionLocal,
    engine,
    init_db,
    init_redis,
    close_redis,
    get_redis,
    redis_client,
)
from app.models import Store, PosTransaction

settings = get_settings()
logger = logging.getLogger(__name__)

# Populated during lifespan; used by /health for uptime_seconds
APP_START_TIME: float = 0.0


# =============================================================================
# Store + POS seed helpers
# =============================================================================

from app.pos_loader import load_store_layout, load_pos_transactions

async def _seed_stores() -> None:
    await load_store_layout()

async def _seed_pos() -> None:
    async with AsyncSessionLocal() as session:
        await load_pos_transactions(session, settings.pos_csv_path)

def _parse_time(t: str):
    from datetime import time as dt_time
    parts = t.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


# =============================================================================
# Lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global APP_START_TIME
    APP_START_TIME = time.monotonic()

    # Industry Standard: Configure the app logger correctly with a StreamHandler
    app_logger = logging.getLogger("app")
    app_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        app_logger.addHandler(handler)
        app_logger.propagate = False

    # 1. Alembic migrations + table creation
    await init_db()
    logger.info("DB migrations applied")

    # 2. Redis
    await init_redis()
    logger.info("Redis connected")

    # 3. Store layout seed
    await _seed_stores()

    # 4. POS transactions seed
    await _seed_pos()

    logger.info("API ready — Store Intelligence v1")
    yield

    # Shutdown
    await close_redis()
    await engine.dispose()
    logger.info("API shutdown complete")


# =============================================================================
# App
# =============================================================================

app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS (dev — restrict origins in prod via settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dashboard static files (mounted before routers so /dashboard path is claimed)
_dashboard_dir = Path(__file__).parent / "dashboard"
if _dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")


# =============================================================================
# Structured logging middleware
# =============================================================================

@app.middleware("http")
async def logging_middleware(request: Request, call_next) -> Response:
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id

    # Best-effort store_id extraction from path: /stores/{store_id}/...
    path_parts = request.url.path.strip("/").split("/")
    store_id = None
    if len(path_parts) >= 2 and path_parts[0] == "stores":
        store_id = path_parts[1]

    t0 = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        # Let the global handler deal with it; log here for latency
        response = Response(status_code=500)

    latency_ms = round((time.monotonic() - t0) * 1000)
    logger.info(
        json.dumps({
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "method": request.method,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
        })
    )
    return response


# =============================================================================
# Global exception handler — no raw stack traces
# =============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.exception(
        "Unhandled exception trace_id=%s endpoint=%s",
        trace_id,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "trace_id": trace_id},
    )


# =============================================================================
# Routers
# =============================================================================

from app.ingestion import router as ingestion_router
from app.metrics   import router as metrics_router
from app.funnel    import router as funnel_router
from app.heatmap   import router as heatmap_router
from app.anomalies import router as anomalies_router
from app.health    import router as health_router

app.include_router(ingestion_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)


# =============================================================================
# SSE — GET /events/stream/{store_id}
# =============================================================================

@app.get("/events/stream/{store_id}", include_in_schema=True)
async def events_stream(store_id: str, request: Request) -> StreamingResponse:
    """
    SSE endpoint. Subscribes to Redis channel `events:{store_id}`.
    Each published JSON message is forwarded as an SSE data frame.
    """
    async def _generator() -> AsyncGenerator[str, None]:
        r = await get_redis()
        pubsub = r.pubsub()
        channel = f"events:{store_id}"
        await pubsub.subscribe(channel)

        try:
            # Send a heartbeat comment immediately so the client knows it's live
            yield ": heartbeat\n\n"

            while True:
                # Check client disconnect
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message.get("type") == "message":
                    data = message["data"]
                    yield f"data: {data}\n\n"
                else:
                    # Keep-alive comment every ~1s
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Nginx: disable buffering for SSE
        },
    )