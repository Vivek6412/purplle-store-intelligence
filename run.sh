#!/usr/bin/env bash
# Process all clips in CLIPS_DIR (or fallback directories) → output/events.jsonl
# Robustly extracts store_id from layout and camera_id from filename.

set -e
LAYOUT=${STORE_LAYOUT_PATH:-data/store_layout.json}
OUTPUT=${OUTPUT_DIR:-output}/events.jsonl

mkdir -p output

# 1. Extract store_id from layout file automatically
store_id=$(python -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    if isinstance(d, list):
        print(d[0].get("store_id", d[0].get("id", "STORE_DEFAULT")))
    else:
        print(d.get("store_id", d.get("id", "STORE_DEFAULT")))
except Exception:
    print("STORE_DEFAULT")
' "$LAYOUT")

# 2. Find clips directory (default, or reviewer's directory)
echo "=== Searching for .mp4 clips ==="
CLIP_COUNT=$(find data/clips -maxdepth 1 -name "*.mp4" 2>/dev/null | wc -l || echo 0)

if [ "$CLIP_COUNT" -gt 0 ]; then
    # Found in default dir, only process these to prevent duplicates
    find data/clips -maxdepth 1 -name "*.mp4" > clips_to_process.tmp
else
    # Fallback: Search recursively everywhere in the project folder
    find . -type f -name "*.mp4" > clips_to_process.tmp
fi

if [ ! -s clips_to_process.tmp ]; then
    echo "No .mp4 clips found anywhere in the project folder!"
    rm -f clips_to_process.tmp
    exit 1
fi

echo "Found the following clips:"
cat clips_to_process.tmp
echo "=========================="

while IFS= read -r clip; do
    # Remove .mp4 and replace spaces with underscores for camera_id
    filename=$(basename "$clip" .mp4)
    camera_id=$(echo "$filename" | tr ' ' '_')
    
    # Dynamically extract store_id from filename if it has a STORE_ prefix
    clip_store_id="$store_id" # Fallback to default store_id
    
    if [[ "$camera_id" == STORE_* ]]; then
        clip_store_id=$(echo "$camera_id" | cut -d'_' -f1-3)
        camera_id=$(echo "$camera_id" | cut -d'_' -f4-)
    elif [[ "$camera_id" == ST1008_* ]]; then
        clip_store_id="ST1008"
        camera_id=$(echo "$camera_id" | cut -d'_' -f2-)
    fi
    
    echo "Processing: $clip (store=$clip_store_id, camera=$camera_id)"
    python detect.py \
        --clip "$clip" \
        --store-id "$clip_store_id" \
        --camera-id "$camera_id" \
        --layout "$LAYOUT" \
        --output "$OUTPUT"
done < clips_to_process.tmp

rm -f clips_to_process.tmp

echo "Done. Events written to $OUTPUT"
wc -l "$OUTPUT"
