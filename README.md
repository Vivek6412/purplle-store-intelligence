# Store Intelligence Platform

End-to-end computer vision and analytics platform for offline retail stores. It ingests raw CCTV footage, extracts physical foot-traffic analytics (visitor counts, demographics, dwell times, and queue depths), and correlates it with POS transaction data in real-time.

## Features
- **Multi-Store Scaling**: Automatically routes and isolates AI logic for unlimited stores based on dynamic layout JSON files.
- **Computer Vision Pipeline**: Utilizes YOLOv8 for detection, ResNet50 for Person Re-Identification, and deterministic hashing for Age/Gender simulation (to maintain real-time performance without heavy ML face extraction).
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
   │   │   └── ST1008_4_billing.mp4
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
   Open `http://localhost:8000/dashboard/` to view the live dashboard and analytics.

## Architecture
See `DESIGN.md` for a comprehensive architectural breakdown and `CHOICES.md` for our technical design decisions.
## Project Structure
```text
/store-intelligence/
├── detect.py
├── tracker.py
├── emit.py
├── run.sh
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
- **GET /stores/{store_id}/metrics**: Aggregate unique visitor counts, dwell times, and conversion rates
- **GET /stores/{store_id}/funnel**: Drop-off rates (Entry -> Zone Visit -> Billing Queue -> Purchase)
- **GET /stores/{store_id}/heatmap**: Zone-based engagement and dwell data
- **GET /stores/{store_id}/anomalies**: Security and queue anomaly logs
- **GET /health**: System diagnostics and uptime

## Known Limitations
- **Resolution Constraint**: DeepFace was initially planned for demographics but was removed in favor of high-throughput deterministic simulation to ensure the tracking pipeline maintained real-time performance on CPU.
- **Occlusion Vulnerability**: Dense crowds (e.g., in a tight billing queue) can momentarily occlude subjects, creating minor risks of identity fragmentation despite the forgiving ReID threshold.
- **Single-Node AI Constraint**: Currently, all inference (YOLOv8 + ResNet50) runs sequentially per frame. Real-world scaling to 50+ stores would require migrating detect.py to a distributed Kafka+GPU cluster architecture.
