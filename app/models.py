import uuid
from datetime import datetime, time
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator
from sqlalchemy import (
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Time,
    Index,
    UniqueConstraint,
    BigInteger,
    Numeric,
    Text,
    text,
    JSON,
    Uuid
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY as PGARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# =================================================================================
# SQLAlchemy ORM Models
# =================================================================================

class Event(Base):
    __tablename__ = "events"
    
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False)
    store_id: Mapped[str] = mapped_column(String(50), nullable=False)
    camera_id: Mapped[str] = mapped_column(String(50), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zone_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_staff: Mapped[bool] = mapped_column(Boolean, index=True, default=False, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    queue_depth: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sku_zone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    session_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        Index("idx_events_store_ts", "store_id", text("timestamp DESC")),
    )

    def __repr__(self) -> str:
        return f"<Event(event_id={self.event_id}, type={self.event_type})>"


class Session(Base):
    __tablename__ = "sessions"
    
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    visitor_id: Mapped[str] = mapped_column(String(20), nullable=False)
    store_id: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_converted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reentry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    zone_sequence: Mapped[Optional[List[str]]] = mapped_column(JSON().with_variant(PGARRAY(String), "postgresql"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=text("CURRENT_TIMESTAMP"), 
        onupdate=text("CURRENT_TIMESTAMP"), 
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("visitor_id", "store_id", "entry_time", name="uq_session_visit"),
        Index("idx_sessions_store", "store_id", text("entry_time DESC")),
    )

    def __repr__(self) -> str:
        return f"<Session(visitor_id={self.visitor_id}, store={self.store_id})>"


class PosTransaction(Base):
    __tablename__ = "pos_transactions"
    
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    basket_value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    matched_visitor: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    __table_args__ = (
        Index("idx_pos_store_ts", "store_id", text("ts DESC")),
    )

    def __repr__(self) -> str:
        return f"<PosTransaction(txn_id={self.transaction_id}, value={self.basket_value})>"


class AnomalyLog(Base):
    __tablename__ = "anomaly_log"
    
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(50), nullable=False)
    anomaly_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    suggested_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_anomaly_store", "store_id", text("detected_at DESC")),
    )

    def __repr__(self) -> str:
        return f"<AnomalyLog(type={self.anomaly_type}, severity={self.severity})>"


class Store(Base):
    __tablename__ = "stores"
    
    store_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    layout_json: Mapped[Dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    open_time: Mapped[time] = mapped_column(Time, nullable=False)
    close_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default='Asia/Kolkata', nullable=False)

    def __repr__(self) -> str:
        return f"<Store(id={self.store_id})>"


# =================================================================================
# Pydantic Schemas
# =================================================================================

class EventMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 1
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    age_bucket: Optional[str] = None
    is_face_hidden: Optional[bool] = None

class EventSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)
    event_id: UUID4
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: Literal[
        "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
        "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
    ]
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @model_validator(mode='before')
    @classmethod
    def translate_sample_schema(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
            
        if 'event_id' in data and 'event_type' in data and data['event_type'] in {"ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"}:
            return data

        evt = data.get("event_type", "").lower()
        if evt == "entry": mapped_type = "ENTRY"
        elif evt == "exit": mapped_type = "EXIT"
        elif evt == "zone_entered": mapped_type = "ZONE_ENTER"
        elif evt == "zone_exited": mapped_type = "ZONE_EXIT"
        elif evt == "queue_completed": mapped_type = "ZONE_DWELL"
        elif evt == "queue_abandoned": mapped_type = "BILLING_QUEUE_ABANDON"
        else: mapped_type = evt.upper()

        store = data.get("store_code") or data.get("store_id")
        
        visitor = data.get("id_token") or data.get("track_id")
        if visitor is not None:
            visitor_str = str(visitor)
            if not visitor_str.startswith("VIS_") and not visitor_str.startswith("ID_"):
                visitor = f"VIS_{visitor_str}"
            else:
                visitor = visitor_str
                
        ts = data.get("event_timestamp") or data.get("event_time") or data.get("queue_exit_ts")
        
        metadata_dict = data.get("metadata", {})
        metadata_dict.update({
            "zone_hotspot_x": data.get("zone_hotspot_x"),
            "zone_hotspot_y": data.get("zone_hotspot_y"),
            "queue_depth": data.get("queue_position_at_join"),
            "gender": data.get("gender") or data.get("gender_pred"),
            "age": data.get("age") or data.get("age_pred"),
            "age_bucket": data.get("age_bucket"),
            "is_face_hidden": data.get("is_face_hidden")
        })

        return {
            "event_id": data.get("queue_event_id") or data.get("event_id") or str(uuid.uuid4()),
            "store_id": store,
            "camera_id": data.get("camera_id"),
            "visitor_id": visitor,
            "event_type": mapped_type,
            "timestamp": ts,
            "zone_id": data.get("zone_id"),
            "dwell_ms": (data.get("wait_seconds", 0) * 1000) or data.get("dwell_ms", 0),
            "is_staff": data.get("is_staff", False),
            "confidence": data.get("confidence", 1.0),
            "metadata": metadata_dict
        }

class IngestRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    events: List[EventSchema] = Field(max_length=500)

class IngestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    ingested: int
    duplicates: int
    errors: List[dict]

class MetricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    store_id: str
    window: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: Dict[str, int]
    queue_depth_current: int
    abandonment_rate: float
    computed_at: datetime

class FunnelStage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    stage: str
    count: int
    drop_off_pct: float

class FunnelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    store_id: str
    stages: List[FunnelStage]

class HeatmapZone(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    zone_id: str
    visit_count: int
    avg_dwell_ms: int
    score: int

class HeatmapResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    store_id: str
    zones: List[HeatmapZone]
    data_confidence: str
    window_sessions: int

class Anomaly(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    anomaly_type: str
    severity: str
    detected_at: datetime
    details: Dict[str, Any]
    suggested_action: str

class AnomaliesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    store_id: str
    anomalies: List[Anomaly]

class StoreHealth(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    last_event_at: datetime
    feed_status: str
    event_count_today: int

class HealthResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    stores: Dict[str, StoreHealth]
    db_status: str
    cache_status: str
    uptime_seconds: float = 0.0
