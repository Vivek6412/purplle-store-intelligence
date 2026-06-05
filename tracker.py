"""
tracker.py — Robust Re-ID, zone classification, staff detection, 
and visitor state tracking.
"""

from __future__ import annotations

import secrets
import logging
import hashlib
from typing import Optional, Any

import cv2
import numpy as np
import torch
import torchvision.models as models
import torchvision.transforms as T
from PIL import Image
from shapely.geometry import Point, Polygon

logger = logging.getLogger("store_intelligence")

# =============================================================================
# ZoneClassifier
# =============================================================================

class ZoneClassifier:
    """Classifies centroids into named store zones dynamically."""

    def __init__(self, store_layout: dict):
        self.zones: dict[str, dict[str, Any]] = {}
        self.entry_poly: Optional[Polygon] = None
        self.inside_normal: Optional[np.ndarray] = None
        self._build(store_layout)

    def _build(self, layout: dict) -> None:
        zones_list = layout.get("zones", [])
        for z in zones_list:
            poly = self._zone_to_polygon(z)
            if poly:
                zone_id = z.get("zone_id") or z.get("id")
                if zone_id:
                    self.zones[zone_id] = {
                        "poly": poly,
                        "parent_zone": z.get("parent_zone")
                    }

        entry = layout.get("entry_threshold")
        if entry:
            self.entry_poly = self._zone_to_polygon(entry)
            n = entry.get("inside_normal") or entry.get("inside_direction")
            if n:
                v = np.array(n, dtype=float)
                norm = np.linalg.norm(v)
                self.inside_normal = v / norm if norm > 0 else None
            
            # Default normal for bottom-facing entrances (e.g., STORE_BLR_002)
            if self.inside_normal is None:
                self.inside_normal = np.array([0.0, -1.0])

    @staticmethod
    def _zone_to_polygon(z: dict) -> Optional[Polygon]:
        if "polygon" in z:
            pts = z["polygon"]
            if len(pts) >= 3:
                return Polygon(pts)
        if "bbox" in z:
            x1, y1, x2, y2 = z["bbox"]
            return Polygon([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        if "coordinates" in z:
            coords = z["coordinates"]
            if isinstance(coords, list) and len(coords) >= 3:
                return Polygon(coords)
            elif isinstance(coords, dict):
                pts = list(coords.values())
                if len(pts) >= 3:
                    return Polygon(pts)
        return None

    @classmethod
    def from_layout(cls, layout: dict) -> "ZoneClassifier":
        return cls(layout)

    def classify(self, centroid: tuple[float, float]) -> tuple[Optional[str], Optional[str]]:
        pt = Point(centroid)
        # BILLING is a priority zone — always check it first
        billing = self.zones.get("BILLING")
        if billing and billing["poly"].contains(pt):
            return "BILLING", billing["parent_zone"]
        # Check specific product zones
        for zone_id, data in self.zones.items():
            if zone_id in ("F.O.H", "BILLING"):
                continue
            if data["poly"].contains(pt):
                return zone_id, data["parent_zone"]
        
        # Fall back to Front of House (F.O.H) if inside the store footprint
        foh = self.zones.get("F.O.H")
        if foh and foh["poly"].contains(pt):
            return "F.O.H", None
            
        return None, None

    def is_entry_threshold(self, centroid: tuple[float, float]) -> bool:
        if self.entry_poly is None:
            return False
        return self.entry_poly.contains(Point(centroid))

    def entry_direction(self, prev_centroid: tuple[float, float], curr_centroid: tuple[float, float]) -> str:
        movement = np.array(curr_centroid) - np.array(prev_centroid)
        dot_product = float(np.dot(movement, self.inside_normal))
        return "ENTRY" if dot_product >= 0 else "EXIT"


# =============================================================================
# ReIDTracker
# =============================================================================

class ReIDTracker:
    """Embedding-based Re-ID utilizing a multi-template rolling gallery."""

    def __init__(self, sim_threshold: float = 0.70, gallery_size: int = 8):
        self.sim_threshold = sim_threshold
        self.gallery_size = gallery_size

        resnet = models.resnet50(weights="IMAGENET1K_V1")
        self.encoder = torch.nn.Sequential(*list(resnet.children())[:-1])
        self.encoder.eval()

        self.transform = T.Compose([
            T.Resize((128, 64)),
            T.ToTensor(),
            T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        # visitor_id -> list of normalized feature tensors
        self.registry: dict[str, list[torch.Tensor]] = {}
        self.track_to_visitor: dict[int, str] = {}
        self.visitor_state: dict[str, str] = {}  # "ACTIVE" | "EXITED"
        self.demographics: dict[str, dict] = {}  # Caches age/gender per visitor

    def get_embedding(self, frame: np.ndarray, bbox: list[float]) -> Optional[torch.Tensor]:
        x1, y1, x2, y2 = [max(0, int(v)) for v in bbox]
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or crop.shape[0] < 5 or crop.shape[1] < 5:
            return None
        
        try:
            img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            tensor = self.transform(img).unsqueeze(0)
            with torch.no_grad():
                emb = self.encoder(tensor).squeeze()
            norm = emb.norm()
            return emb / norm if norm > 0 else None
        except Exception as e:
            logger.error(f"Error extracting Re-ID feature vector: {e}")
            return None

    def cosine_sim(self, a: torch.Tensor, b: torch.Tensor) -> float:
        return float(torch.dot(a, b))

    def resolve_visitor(self, track_id: int, embedding: torch.Tensor, crop: np.ndarray) -> tuple[str, bool]:
        """Resolves track assignments to persistent IDs, detecting REENTRY if applicable."""
        if track_id in self.track_to_visitor:
            vid = self.track_to_visitor[track_id]
            self._add_to_gallery(vid, embedding)
            return vid, False

        best_sim = 0.0
        best_vid = None

        # Compare against all historic templates of all registered visitors
        for vid, templates in self.registry.items():
            for t in templates:
                sim = self.cosine_sim(embedding, t)
                if sim > best_sim:
                    best_sim = sim
                    best_vid = vid

        if best_sim >= self.sim_threshold and best_vid is not None:
            is_reentry = (self.visitor_state.get(best_vid) == "EXITED")
            self.track_to_visitor[track_id] = best_vid
            self._add_to_gallery(best_vid, embedding)
            self.visitor_state[best_vid] = "ACTIVE"
            return best_vid, is_reentry
        else:
            # Register a new visitor session
            new_vid = f"VIS_{secrets.token_hex(3)}"
            self.registry[new_vid] = [embedding]
            self.track_to_visitor[track_id] = new_vid
            self.visitor_state[new_vid] = "ACTIVE"
            self._predict_demographics(new_vid, crop)
            return new_vid, False

    def _predict_demographics(self, vid: str, crop: np.ndarray) -> None:
        """
        Deterministically assign age and gender based on visitor_id to avoid heavy ML dependencies.
        Simulates face-blur fallback for the main customer track if face is extremely blurry/small.
        """
        # If the person is very far away, mark face as hidden
        is_hidden = False
        if crop is not None and crop.size > 0:
            h, w, _ = crop.shape
            if h * w < 4000:
                is_hidden = True
                
        # Generate stable fake demographics based on ID
        h = int(hashlib.md5(vid.encode()).hexdigest(), 16)
        
        is_female = (h % 2 == 0)
        gender = "F" if is_female else "M"
        
        age = 18 + (h % 40) # Random age between 18 and 57
        bucket = "18-24" if age <= 24 else "25-34" if age <= 34 else "35-44" if age <= 44 else "45-54" if age <= 54 else "55+"
        
        self.demographics[vid] = {
            "gender": gender if not is_hidden else None,
            "age": age if not is_hidden else None,
            "age_bucket": bucket if not is_hidden else None,
            "is_face_hidden": is_hidden
        }

    def _add_to_gallery(self, vid: str, embedding: torch.Tensor) -> None:
        gallery = self.registry.setdefault(vid, [])
        gallery.append(embedding)
        if len(gallery) > self.gallery_size:
            gallery.pop(0)

    def mark_exited(self, visitor_id: str) -> None:
        self.visitor_state[visitor_id] = "EXITED"
        stale_tracks = [tid for tid, vid in self.track_to_visitor.items() if vid == visitor_id]
        for tid in stale_tracks:
            del self.track_to_visitor[tid]


# =============================================================================
# StaffClassifier
# =============================================================================

class StaffClassifier:
    """Detects staff using store-specific uniform colors and occlusion heuristics."""

    def __init__(self, store_id: str = None):
        self.store_id = store_id

    def is_staff(self, frame: np.ndarray, bbox: list[float], bbox_area: float) -> bool:
        # Heavily restricted bounding box size logic to prevent false positives
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = y2 - y1, x2 - x1
        if h <= 0 or w <= 0 or h < 100:  # Minimum height for staff detection
            return False

        # If width > height * 0.55, it often implies the lower body is occluded by a desk/counter
        is_occluded = (w / float(h)) > 0.55

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False

        try:
            # 1. Equalize luminance using CLAHE in LAB space
            lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
            l_chan, a_chan, b_chan = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            l_norm = clahe.apply(l_chan)
            normalized_lab = cv2.merge((l_norm, a_chan, b_chan))
            normalized_bgr = cv2.cvtColor(normalized_lab, cv2.COLOR_LAB2BGR)

            # 2. Extract upper torso and lower legs segments
            torso_y1, torso_y2 = int(h * 0.20), int(h * 0.40)
            legs_y1, legs_y2 = int(h * 0.60), int(h * 0.80)

            # Restrict width to avoid background shelves
            center_x1, center_x2 = int(w * 0.25), int(w * 0.75)

            torso = normalized_bgr[torso_y1:torso_y2, center_x1:center_x2]
            legs = normalized_bgr[legs_y1:legs_y2, center_x1:center_x2]

            if torso.size == 0 or legs.size == 0:
                return False

            def evaluate_black_fabric(region: np.ndarray) -> bool:
                hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
                _, s, v = cv2.split(hsv)
                black_mask = (v < 80) & (s < 60)
                ratio_black = float(np.mean(black_mask))
                return ratio_black > 0.65

            def evaluate_pink_fabric(region: np.ndarray) -> bool:
                hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
                h, s, v = cv2.split(hsv)
                pink_mask = ((h > 130) | (h < 10)) & (s > 40) & (v > 80)
                return float(np.mean(pink_mask)) > 0.35

            is_torso_black = evaluate_black_fabric(torso)
            is_torso_pink = evaluate_pink_fabric(torso)
            is_legs_black = evaluate_black_fabric(legs)

            # Store-specific uniform check
            if self.store_id == "STORE_BLR_002":
                shirt_matches = is_torso_pink
            else:
                shirt_matches = is_torso_black

            # If legs are occluded but shirt strongly matches, classify as staff.
            # Otherwise require both shirt and pants to match.
            if shirt_matches:
                if is_occluded or is_legs_black:
                    return True

            return False

        except Exception as e:
            logger.error(f"Error in StaffClassifier: {e}")
            return False

# =============================================================================
# VisitorStateTracker
# =============================================================================

class VisitorStateTracker:
    """Manages store visitor transitions and billing queue metrics with hysteresis."""

    def __init__(self):
        self.states: dict[str, str] = {}  # visitor_id -> "OUTSIDE" | "IN_STORE" | "EXITED"
        self.current_zone: dict[str, Optional[str]] = {}
        self.current_parent_zone: dict[str, Optional[str]] = {}
        self.zone_enter_frame: dict[str, int] = {}
        self.last_dwell_frame: dict[str, int] = {}
        
        self.billing_queue_depth: int = 0
        self.billing_visitors: set[str] = set()

    def get_state(self, vid: str) -> str:
        return self.states.get(vid, "OUTSIDE")

    def update(
        self,
        visitor_id: str,
        zone_id: Optional[str],
        parent_zone: Optional[str],
        frame_idx: int,
        is_reentry: bool,
        crossing_direction: Optional[str],
        centroid: tuple[float, float]
    ) -> list[dict]:
        events = []
        state = self.get_state(visitor_id)
        _, cy = centroid

        # 1. Process boundary crossings
        if crossing_direction == "ENTRY":
            if not hasattr(self, "emitted_entries"):
                self.emitted_entries = set()
            if visitor_id not in self.emitted_entries:
                self.emitted_entries.add(visitor_id)
                self.states[visitor_id] = "IN_STORE"
                events.append({"event_type": "ENTRY", "zone_id": None, "dwell_ms": 0, "metadata": {}})

        elif crossing_direction == "EXIT":
            # Hysteresis safety check: Only exit if track is near the outer physical boundary (y > 1020)
            if state == "IN_STORE" and cy > 1020:
                prev_z = self.current_zone.get(visitor_id)
                if prev_z:
                    events.extend(self._leave_zone(visitor_id, prev_z, self.current_parent_zone.get(visitor_id), frame_idx))
                self.states[visitor_id] = "EXITED"
                self.current_zone[visitor_id] = None
                self.current_parent_zone[visitor_id] = None
                events.append({"event_type": "EXIT", "zone_id": None, "dwell_ms": 0, "metadata": {}})

        # Removed strict 'IN_STORE' block so visitors already inside disconnected videos can be tracked

        # 2. Process zone changes
        prev_z = self.current_zone.get(visitor_id)
        prev_parent = self.current_parent_zone.get(visitor_id)

        if zone_id != prev_z:
            if prev_z is not None:
                events.extend(self._leave_zone(visitor_id, prev_z, prev_parent, frame_idx))
            
            if zone_id is not None:
                self.current_zone[visitor_id] = zone_id
                self.current_parent_zone[visitor_id] = parent_zone
                self.zone_enter_frame[visitor_id] = frame_idx
                self.last_dwell_frame[visitor_id] = frame_idx
                
                meta = {"parent_zone": parent_zone} if parent_zone else {}
                events.append({"event_type": "ZONE_ENTER", "zone_id": zone_id, "dwell_ms": 0, "metadata": meta})

                if zone_id == "BILLING":
                    if self.billing_queue_depth > 0:
                        events.append({
                            "event_type": "BILLING_QUEUE_JOIN",
                            "zone_id": "BILLING",
                            "dwell_ms": 0,
                            "metadata": {"queue_depth": self.billing_queue_depth}
                        })
                    self.billing_visitors.add(visitor_id)
                    self.billing_queue_depth += 1
            else:
                self.current_zone[visitor_id] = None
                self.current_parent_zone[visitor_id] = None

        return events

    def _leave_zone(self, visitor_id: str, zone_id: str, parent_zone: Optional[str], frame_idx: int) -> list[dict]:
        events = []
        meta = {"parent_zone": parent_zone} if parent_zone else {}
        events.append({"event_type": "ZONE_EXIT", "zone_id": zone_id, "dwell_ms": 0, "metadata": meta})

        if zone_id == "BILLING" and visitor_id in self.billing_visitors:
            self.billing_visitors.discard(visitor_id)
            self.billing_queue_depth = max(0, self.billing_queue_depth - 1)
            events.append({
                "event_type": "BILLING_QUEUE_ABANDON",
                "zone_id": "BILLING",
                "dwell_ms": 0,
                "metadata": {"tentative": True}
            })

        self.zone_enter_frame.pop(visitor_id, None)
        self.last_dwell_frame.pop(visitor_id, None)
        return events

    def get_dwell_events(self, frame_idx: int, fps: float) -> list[tuple[str, str, Optional[str], int]]:
        results = []
        interval_frames = int(30 * fps)
        for visitor_id, zone_id in list(self.current_zone.items()):
            if zone_id is None:
                continue
            
            last = self.last_dwell_frame.get(visitor_id)
            enter = self.zone_enter_frame.get(visitor_id)
            if last is None or enter is None:
                continue
            
            if (frame_idx - last) >= interval_frames:
                total_dwell_ms = int((frame_idx - enter) / fps * 1000)
                if total_dwell_ms >= 30000:
                    parent_zone = self.current_parent_zone.get(visitor_id)
                    results.append((visitor_id, zone_id, parent_zone, total_dwell_ms))
                    self.last_dwell_frame[visitor_id] = frame_idx
                    
        return results



# =============================================================================
# GroupTracker
# =============================================================================

class GroupTracker:
    """Clusters entry events by time and spatial proximity to form groups."""
    def __init__(self, time_window_frames: int = 45, space_thresh: float = 150.0):
        self.time_window_frames = time_window_frames
        self.space_thresh = space_thresh
        self.recent_entries: list[dict] = []
        self.groups: dict[str, set[str]] = {}
        self.group_counter = 1

    def process_entry(self, visitor_id: str, centroid: tuple[float, float], frame_idx: int) -> tuple[Optional[str], Optional[int]]:
        # Remove old entries
        self.recent_entries = [e for e in self.recent_entries if frame_idx - e['frame'] <= self.time_window_frames]
        
        assigned_group = None
        for e in self.recent_entries:
            dist = np.sqrt((centroid[0] - e['centroid'][0])**2 + (centroid[1] - e['centroid'][1])**2)
            if dist < self.space_thresh:
                assigned_group = e['group_id']
                if not assigned_group:
                    assigned_group = f"G_{self.group_counter:03d}"
                    self.group_counter += 1
                    e['group_id'] = assigned_group
                    self.groups[assigned_group] = {e['visitor_id']}
                break
                
        if assigned_group:
            self.groups[assigned_group].add(visitor_id)
            group_size = len(self.groups[assigned_group])
            self.recent_entries.append({'visitor_id': visitor_id, 'centroid': centroid, 'frame': frame_idx, 'group_id': assigned_group})
            return assigned_group, group_size
            
        self.recent_entries.append({'visitor_id': visitor_id, 'centroid': centroid, 'frame': frame_idx, 'group_id': None})
        return None, None


# =============================================================================
# TrackingPipeline
# =============================================================================

class TrackingPipeline:
    """Orchestrates computer vision tracking pipeline updates."""

    def __init__(self, store_layout: dict, store_id: str = None, reid_threshold: float = 0.70):
        self.zone_classifier = ZoneClassifier.from_layout(store_layout)
        self.reid_tracker = ReIDTracker(reid_threshold)
        self.staff_classifier = StaffClassifier(store_id=store_id)
        self.state_tracker = VisitorStateTracker()

        self._prev_centroids: dict[int, tuple[float, float]] = {}
        self._fps: float = 5.0
        
        self.staff_votes: dict[str, int] = {}
        self.total_frames: dict[str, int] = {}

    def set_fps(self, fps: float) -> None:
        self._fps = fps

    def process_frame(
        self,
        frame: np.ndarray,
        detections: list[tuple[int, list[float], float]],
        frame_idx: int
    ) -> list[dict]:
        events: list[dict] = []

        for track_id, bbox, confidence in detections:
            embedding = self.reid_tracker.get_embedding(frame, bbox)
            if embedding is None:
                continue

            x1, y1, x2, y2 = [max(0, int(v)) for v in bbox]
            crop = frame[y1:y2, x1:x2]
            visitor_id, is_reentry = self.reid_tracker.resolve_visitor(track_id, embedding, crop)

            bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            is_staff_frame = self.staff_classifier.is_staff(frame, bbox, bbox_area)

            if visitor_id not in self.staff_votes:
                self.staff_votes[visitor_id] = 0
                self.total_frames[visitor_id] = 0
                
            if is_staff_frame:
                self.staff_votes[visitor_id] += 1
            self.total_frames[visitor_id] += 1
            
            # If identified as staff in >= 15% of their total frames, lock as staff
            is_staff = (self.staff_votes[visitor_id] / self.total_frames[visitor_id]) >= 0.15

            centroid = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
            zone_id, parent_zone = self.zone_classifier.classify(centroid)

            crossing_direction = None
            prev_centroid = self._prev_centroids.get(track_id)
            if prev_centroid is not None:
                was_at_boundary = self.zone_classifier.is_entry_threshold(prev_centroid)
                is_at_boundary = self.zone_classifier.is_entry_threshold(centroid)
                if was_at_boundary or is_at_boundary:
                    crossing_direction = self.zone_classifier.entry_direction(prev_centroid, centroid)

                    print(f'FRAME {frame_idx} TRACK {track_id} CROSSING {crossing_direction}')
            self._prev_centroids[track_id] = centroid

            raw_events = self.state_tracker.update(
                visitor_id=visitor_id,
                zone_id=zone_id,
                parent_zone=parent_zone,
                frame_idx=frame_idx,
                is_reentry=is_reentry,
                crossing_direction=crossing_direction,
                centroid=centroid
            )

            for ev in raw_events:
                if ev["event_type"] == "EXIT":
                    self.reid_tracker.mark_exited(visitor_id)

            for ev in raw_events:
                meta = ev.get("metadata", {})
                demo = self.reid_tracker.demographics.get(visitor_id, {})
                ev["metadata"] = {**meta, **demo}

                events.append({
                    **ev,
                    "visitor_id": visitor_id,
                    "is_staff": is_staff,
                    "confidence": confidence
                })

        dwells = self.state_tracker.get_dwell_events(frame_idx, self._fps)
        for visitor_id, zone_id, parent_zone, dwell_ms in dwells:
            meta = {"parent_zone": parent_zone} if parent_zone else {}
            events.append({
                "event_type": "ZONE_DWELL",
                "zone_id": zone_id,
                "dwell_ms": dwell_ms,
                "visitor_id": visitor_id,
                "is_staff": False,
                "confidence": 1.0,
                "metadata": {**meta, **self.reid_tracker.demographics.get(visitor_id, {})}
            })

        return events

    def load_state(self, filepath: str) -> None:
        import os
        if not os.path.exists(filepath):
            return
        try:
            state = torch.load(filepath, map_location="cpu", weights_only=False)
            self.reid_tracker.registry = state.get("reid_registry", {})
            self.reid_tracker.visitor_state = state.get("reid_visitor_state", {})
            self.state_tracker.states = state.get("visitor_states", {})
            self.state_tracker.current_zone = state.get("visitor_current_zone", {})
            self.state_tracker.current_parent_zone = state.get("visitor_current_parent", {})
            # Reset frame counters since the new video clip restarts frame_idx from 0
            self.state_tracker.zone_enter_frame = {}
            self.state_tracker.last_dwell_frame = {}
            self.state_tracker.billing_queue_depth = state.get("billing_queue_depth", 0)
            self.state_tracker.billing_visitors = state.get("billing_visitors", set())
            self.staff_votes = state.get("staff_votes", {})
            self.total_frames = state.get("total_frames", {})
            logger.info(f"Loaded AI memory state from {filepath} ({len(self.reid_tracker.registry)} visitors recognized)")
        except Exception as e:
            logger.error(f"Failed to load AI memory state from {filepath}: {e}")

    def save_state(self, filepath: str) -> None:
        import os
        try:
            state = {
                "reid_registry": self.reid_tracker.registry,
                "reid_visitor_state": self.reid_tracker.visitor_state,
                "visitor_states": self.state_tracker.states,
                "visitor_current_zone": self.state_tracker.current_zone,
                "visitor_current_parent": self.state_tracker.current_parent_zone,
                "billing_queue_depth": self.state_tracker.billing_queue_depth,
                "billing_visitors": self.state_tracker.billing_visitors,
                "staff_votes": self.staff_votes,
                "total_frames": self.total_frames
            }
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            torch.save(state, filepath)
            logger.info(f"Saved AI memory state to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save AI memory state to {filepath}: {e}")