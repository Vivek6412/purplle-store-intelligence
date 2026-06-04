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
                        ev["timestamp"] = datetime.now(timezone.utc).isoformat()
                        events_batch.append(ev)
                    except Exception as e:
                        pass # partial JSON lines might occur while writing
                
                file_pointers[file_path] = f.tell()
        except Exception as e:
            pass
            
    if events_batch:
        try:
            res = requests.post(API_URL, json={"events": events_batch})
            print(f"[LIVE] Streamed {len(events_batch)} events! Response: {res.status_code}")
            time.sleep(0.5) 
        except Exception as e:
            pass
    else:
        time.sleep(1)
