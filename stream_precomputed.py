import json
import time
import requests
import uuid
from datetime import datetime, timezone

API_URL = "http://localhost:8000/events/ingest"

print("[*] Starting streaming ingestor for pre-computed events with FRESH UUIDs...")

count = 0
with open("output/test_events.jsonl", "r") as f:
    for line in f:
        if not line.strip(): continue
        try:
            ev = json.loads(line)
            # Shift timestamp to NOW
            ev["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            # CRITICAL FIX: Generate a NEW event_id so Postgres doesn't ignore it as a duplicate
            ev["event_id"] = str(uuid.uuid4())
            
            requests.post(API_URL, json={"events": [ev]})
            count += 1
            print(f"[LIVE] Streamed event {count}: {ev['event_type']} for {ev['visitor_id']}")
            time.sleep(1.0)
        except Exception as e:
            pass
            
print("[*] Streaming complete!")
