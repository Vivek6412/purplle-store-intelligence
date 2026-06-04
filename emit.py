"""
emit.py — Schema serialization and stream-compliant JSONL writer.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

class EventEmitter:
    """Manages active file writing operations for output streams."""

    def __init__(
        self,
        output_path: str,
        store_id: str,
        camera_id: str,
        clip_start_time: datetime,
        fps: float
    ):
        self.output_path = output_path
        self.store_id = store_id
        self.camera_id = camera_id
        self.clip_start_time = clip_start_time
        self.fps = fps
        self.session_sequences: dict[str, int] = {}
        self.stats = {"emitted_events": 0}

        # Initialize output file structure
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        self.file_handle = open(output_path, "a", encoding="utf-8")

    def emit(
        self,
        event_type: str,
        visitor_id: str,
        frame_idx: int,
        zone_id: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        confidence: float = 1.0,
        metadata: Optional[dict[str, Any]] = None
    ) -> None:
        """Serializes tracking metrics into strict schema-compliant JSONL events."""
        time_offset = timedelta(seconds=frame_idx / self.fps)
        timestamp_str = (self.clip_start_time + time_offset).isoformat() + "Z"

        seq = self.session_sequences.get(visitor_id, 0) + 1
        self.session_sequences[visitor_id] = seq

        meta_payload = metadata or {}
        if "session_seq" not in meta_payload:
            meta_payload["session_seq"] = seq

        if event_type in ["ENTRY", "EXIT", "REENTRY"]:
            event_payload = {
                "event_type": event_type.lower(),
                "id_token": visitor_id,
                "store_code": self.store_id,
                "camera_id": self.camera_id,
                "event_timestamp": timestamp_str,
                "is_staff": is_staff,
                "gender_pred": meta_payload.get("gender"),
                "age_pred": meta_payload.get("age"),
                "age_bucket": meta_payload.get("age_bucket"),
                "is_face_hidden": meta_payload.get("is_face_hidden"),
                "group_id": None,
                "group_size": None,
                "confidence": round(confidence, 3)
            }
        elif event_type in ["ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL"]:
            try:
                track_id = int(visitor_id.replace("VIS_", ""), 16) % 1000000
            except:
                track_id = hash(visitor_id) % 1000000
                
            event_payload = {
                "event_type": "zone_entered" if event_type == "ZONE_ENTER" else "zone_exited" if event_type == "ZONE_EXIT" else "zone_dwell",
                "track_id": track_id,
                "store_id": self.store_id,
                "camera_id": self.camera_id,
                "zone_id": zone_id,
                "zone_name": zone_id,
                "zone_type": "SHELF" if zone_id and zone_id != "BILLING" else "ZONE",
                "is_revenue_zone": "Yes" if zone_id and zone_id != "BILLING" else "No",
                "event_time": timestamp_str,
                "zone_hotspot_x": meta_payload.get("zone_hotspot_x"),
                "zone_hotspot_y": meta_payload.get("zone_hotspot_y"),
                "gender": meta_payload.get("gender"),
                "age": meta_payload.get("age"),
                "age_bucket": meta_payload.get("age_bucket"),
                "is_staff": is_staff,
                "confidence": round(confidence, 3)
            }
            if event_type == "ZONE_DWELL":
                event_payload["dwell_ms"] = dwell_ms
        elif event_type in ["BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"]:
            try:
                track_id = int(visitor_id.replace("VIS_", ""), 16) % 1000000
            except:
                track_id = hash(visitor_id) % 1000000
                
            event_payload = {
                "queue_event_id": str(uuid.uuid4()),
                "event_type": "queue_completed" if event_type == "BILLING_QUEUE_JOIN" else "queue_abandoned",
                "track_id": track_id,
                "store_id": self.store_id,
                "camera_id": self.camera_id,
                "zone_id": zone_id or "BILLING",
                "zone_name": "Billing Counter Queue",
                "zone_type": "BILLING",
                "is_revenue_zone": "Yes",
                "queue_join_ts": timestamp_str,
                "queue_served_ts": None,
                "queue_exit_ts": timestamp_str,
                "wait_seconds": int(dwell_ms / 1000),
                "queue_position_at_join": meta_payload.get("queue_depth"),
                "abandoned": event_type == "BILLING_QUEUE_ABANDON",
                "zone_hotspot_x": meta_payload.get("zone_hotspot_x"),
                "zone_hotspot_y": meta_payload.get("zone_hotspot_y"),
                "gender": meta_payload.get("gender"),
                "age": meta_payload.get("age"),
                "age_bucket": meta_payload.get("age_bucket"),
                "is_staff": is_staff,
                "confidence": round(confidence, 3)
            }
        else:
            event_payload = {
                "event_id": str(uuid.uuid4()),
                "store_id": self.store_id,
                "camera_id": self.camera_id,
                "visitor_id": visitor_id,
                "event_type": event_type,
                "timestamp": timestamp_str,
                "zone_id": zone_id,
                "dwell_ms": dwell_ms,
                "is_staff": is_staff,
                "confidence": round(confidence, 3),
                "metadata": meta_payload
            }

        self.file_handle.write(json.dumps(event_payload) + "\n")
        self.stats["emitted_events"] += 1

    def close(self) -> None:
        if not self.file_handle.closed:
            self.file_handle.flush()
            self.file_handle.close()


# =============================================================================
# Helper Utilities
# =============================================================================

def make_entry_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    emitter.emit("ENTRY", visitor_id, frame_idx, is_staff=is_staff, confidence=confidence, metadata=metadata)

def make_exit_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    emitter.emit("EXIT", visitor_id, frame_idx, is_staff=is_staff, confidence=confidence, metadata=metadata)

def make_zone_enter_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, zone_id: str, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    emitter.emit("ZONE_ENTER", visitor_id, frame_idx, zone_id=zone_id, is_staff=is_staff, confidence=confidence, metadata=metadata)

def make_zone_exit_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, zone_id: str, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    emitter.emit("ZONE_EXIT", visitor_id, frame_idx, zone_id=zone_id, is_staff=is_staff, confidence=confidence, metadata=metadata)

def make_zone_dwell_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, zone_id: str, dwell_ms: int, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    emitter.emit("ZONE_DWELL", visitor_id, frame_idx, zone_id=zone_id, dwell_ms=dwell_ms, is_staff=is_staff, confidence=confidence, metadata=metadata)

def make_billing_queue_join_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, queue_depth: int, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    meta = metadata or {}
    meta["queue_depth"] = queue_depth
    emitter.emit("BILLING_QUEUE_JOIN", visitor_id, frame_idx, zone_id="BILLING", is_staff=is_staff, confidence=confidence, metadata=meta)

def make_billing_queue_abandon_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    emitter.emit("BILLING_QUEUE_ABANDON", visitor_id, frame_idx, zone_id="BILLING", is_staff=is_staff, confidence=confidence, metadata=metadata)

def make_reentry_event(emitter: EventEmitter, visitor_id: str, frame_idx: int, is_staff: bool, confidence: float, metadata: Optional[dict] = None) -> None:
    emitter.emit("REENTRY", visitor_id, frame_idx, is_staff=is_staff, confidence=confidence, metadata=metadata)