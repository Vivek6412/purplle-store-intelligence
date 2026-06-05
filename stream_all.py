import json
import time
import requests
from datetime import datetime, timezone
import os

API_URL = os.environ.get("API_URL", "http://localhost:8000/events/ingest")
# stream_all.py tails events.jsonl
FILES = ["output/events.jsonl"]
file_pointers = {f: 0 for f in FILES}

print(f"[*] Starting live stream ingestor for 5 cameras to {API_URL}...")

while True:
    events_batch = []
    
    for file_path in FILES:
        if not os.path.exists(file_path):
            continue
            
        try:
            with open(file_path, 'r') as f:
                f.seek(file_pointers[file_path])
                for line in f:
                    if not line.strip(): continue
                    try:
                        ev = json.loads(line)
                        now_str = datetime.now(timezone.utc).isoformat()
                        if "event_time" in ev: ev["event_time"] = now_str
                        if "event_timestamp" in ev: ev["event_timestamp"] = now_str
                        if "queue_exit_ts" in ev: ev["queue_exit_ts"] = now_str
                        if "queue_join_ts" in ev: ev["queue_join_ts"] = now_str
                        events_batch.append(ev)
                    except Exception as e:
                        pass # partial JSON lines might occur while writing
                
                file_pointers[file_path] = f.tell()
        except Exception as e:
            pass
            
    if events_batch:
        chunk_size = 500
        for i in range(0, len(events_batch), chunk_size):
            chunk = events_batch[i:i+chunk_size]
            try:
                res = requests.post(API_URL, json={"events": chunk})
                print(f"[LIVE] Streamed {len(chunk)} events! Response: {res.status_code}")
                if res.status_code != 200:
                    print(res.text)
                time.sleep(0.1) 
            except Exception as e:
                print(f"Error posting: {e}")
        time.sleep(0.5)
    else:
        time.sleep(1)
