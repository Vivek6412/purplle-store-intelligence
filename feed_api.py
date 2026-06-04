import argparse
import json
import csv
import urllib.request
import urllib.error
import time
from datetime import datetime, timedelta
from tqdm import tqdm

def retract_false_abandons(events: list, pos_csv_path: str) -> list:
    """
    For each BILLING_QUEUE_ABANDON event:
      Check if a POS transaction exists at same store within
      [event.timestamp, event.timestamp + 5min]
      If yes: remove the event (visitor actually purchased)
    Returns cleaned events list.
    """
    try:
        with open(pos_csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            pos_txns = list(reader)
    except FileNotFoundError:
        print(f"POS CSV {pos_csv_path} not found. Skipping retract phase.")
        return events

    # Index pos transactions by store
    txns_by_store = {}
    for t in pos_txns:
        store = t["store_id"]
        # Ensure UTC timezone awareness
        ts_str = t["ts"]
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        ts = datetime.fromisoformat(ts_str)
        if store not in txns_by_store:
            txns_by_store[store] = []
        txns_by_store[store].append(ts)
        
    cleaned_events = []
    retracted = 0
    
    for ev in events:
        evt = ev.get("event_type", "")
        if evt == "BILLING_QUEUE_ABANDON" or evt == "queue_abandoned":
            store = ev.get("store_id") or ev.get("store_code")
            ev_ts_str = ev.get("timestamp") or ev.get("event_timestamp") or ev.get("event_time") or ev.get("queue_exit_ts")
            if ev_ts_str and ev_ts_str.endswith("Z"):
                ev_ts_str = ev_ts_str[:-1] + "+00:00"
            ev_ts = datetime.fromisoformat(ev_ts_str)
            
            # Check 5min window
            found = False
            for ts in txns_by_store.get(store, []):
                if ev_ts <= ts <= ev_ts + timedelta(minutes=5):
                    found = True
                    break
                    
            if found:
                retracted += 1
                continue # Skip adding to cleaned_events
                
        cleaned_events.append(ev)
        
    print(f"Retracted {retracted} false BILLING_QUEUE_ABANDON events.")
    return cleaned_events


def feed_api(events_path: str, api_url: str, pos_csv_path: str = "data/pos_transactions.csv", batch_size: int = 500, dry_run: bool = False):
    """
    Read events.jsonl line by line.
    Batch into groups of batch_size.
    POST each batch to {api_url}/events/ingest.
    Retry up to 3 times on 5xx.
    Print summary: total sent, ingested, duplicates, errors.
    """
    events = []
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
    except FileNotFoundError:
        print(f"Events file {events_path} not found.")
        return
        
    # Retract false abandons before posting
    events = retract_false_abandons(events, pos_csv_path)
    
    if dry_run:
        print(f"Dry run. Would send {len(events)} events.")
        return
        
    batches = [events[i:i + batch_size] for i in range(0, len(events), batch_size)]
    
    total_ingested = 0
    total_duplicates = 0
    total_errors = 0
    
    req_url = f"{api_url.rstrip('/')}/events/ingest"
    
    for batch in tqdm(batches, desc="Posting batches"):
        payload = json.dumps({"events": batch}).encode("utf-8")
        req = urllib.request.Request(
            req_url,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        
        success = False
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    res_body = response.read()
                    res_data = json.loads(res_body)
                    total_ingested += res_data.get("ingested", 0)
                    total_duplicates += res_data.get("duplicates", 0)
                    total_errors += len(res_data.get("errors", []))
                    success = True
                    break
            except urllib.error.HTTPError as e:
                if e.code >= 500:
                    time.sleep(1)
                    continue
                else:
                    print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
                    break
            except Exception as e:
                time.sleep(1)
                continue
                
        if not success:
            print(f"Failed to post batch of size {len(batch)} after 3 attempts.")
            
    print("\n=== Feed API Summary ===")
    print(f"Total events sent: {len(events)}")
    print(f"Ingested: {total_ingested}")
    print(f"Duplicates: {total_duplicates}")
    print(f"Errors: {total_errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Feed events.jsonl to the API")
    parser.add_argument("--events", required=True, help="Path to events.jsonl")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for POST")
    parser.add_argument("--dry-run", action="store_true", help="Parse and process but do not POST")
    parser.add_argument("--pos", default="data/pos_transactions.csv", help="Path to POS CSV for abandon retraction")
    args = parser.parse_args()
    
    feed_api(args.events, args.api_url, args.pos, args.batch_size, args.dry_run)
