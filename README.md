# Store Intelligence Platform

End-to-end computer vision and analytics platform for offline retail stores. It ingests raw CCTV footage, extracts physical foot-traffic analytics (visitor counts, demographics, dwell times, and queue depths), and correlates it with POS transaction data in real-time.

## Features
- **Multi-Store Scaling**: Automatically routes and isolates AI logic for unlimited stores based on dynamic layout JSON files.
- **Computer Vision Pipeline**: Utilizes YOLOv8 for detection, OSNet for Person Re-Identification, and DeepFace for Age/Gender prediction.
- **Identity Consistency**: Tracks users across multiple cameras using isolated AI Memory state files (`.pt`) to prevent cross-store hallucination.
- **Live Stream Simulation**: Throttles historical AI events into the API endpoint to perfectly simulate real-time dashboards.
- **Chaotic Schema Adapter**: Validates and normalizes messy JSON streams into structured PostgreSQL tables.

## Requirements
- Docker
- Docker Compose

## Installation & Setup

1. **Clone the repository.**
2. **Prepare the Data**:
   Ensure your directory structure looks like this:
   ```text
   /store-intelligence/
   ├── data/
   │   ├── clips/
   │   │   ├── STORE_BLR_001_CAM_1.mp4
   │   │   └── ST1008_billing.mp4
   │   ├── layouts/
   │   │   ├── store_1_layout.json
   │   │   └── store_2_layout.json
   │   └── pos_transactions.csv
   ```
3. **Configure Environment Variables**:
   A `.env` file is provided in the root directory. Ensure it matches your deployment needs.

4. **Launch the Stack**:
   Run the following command to build the CV environment and boot the API and Database:
   ```bash
   docker compose up -d --build
   ```

5. **Access the Dashboard**:
   Open `http://localhost:8000` to view the live dashboard and analytics.

## Architecture
See `DESIGN.md` for a comprehensive architectural breakdown and `CHOICES.md` for our technical design decisions.
## Project Structure
```text
/store-intelligence/
├── pipeline/
│   ├── detect.py
│   ├── tracker.py
│   ├── emit.py
│   └── run.sh
├── app/
│   ├── main.py
│   ├── models.py
│   ├── ingestion.py
│   ├── metrics.py
│   ├── funnel.py
│   ├── anomalies.py
│   └── health.py
├── data/
│   ├── clips/
│   └── layouts/
├── docs/
├── docker-compose.yml
├── README.md
├── DESIGN.md
└── CHOICES.md
```

## API Endpoints
The backend provides robust analytics endpoints to drive the frontend dashboard:
- **POST /events/ingest**: High-throughput receiver for incoming AI telemetry
- **GET /events/stream/{store_id}**: Server-Sent Events (SSE) live feed
- **GET /metrics/visitors**: Aggregate unique visitor counts
- **GET /metrics/zones**: Dwell times and zone-based engagement data
- **GET /metrics/queues**: Billing counter queue wait times
- **GET /metrics/conversion**: Real-time sales vs. foot-traffic conversion rate
- **GET /funnel**: Drop-off rates (Entry -> Browsing -> Billing)
- **GET /anomalies**: Security and queue anomaly logs
- **GET /health**: System diagnostics and uptime

## Known Limitations
- **Resolution Constraint**: DeepFace requires a moderately clear, frontal face shot to accurately predict age/gender. The system utilizes a fallback hash if a face is not visible.
- **Occlusion Vulnerability**: Dense crowds (e.g., in a tight billing queue) can momentarily occlude subjects, creating minor risks of identity fragmentation despite the forgiving ReID threshold.
- **Single-Node AI Constraint**: Currently, all inference (YOLOv8 + OSNet + DeepFace) runs sequentially per frame. Real-world scaling to 50+ stores would require migrating detect.py to a distributed Kafka+GPU cluster architecture.
