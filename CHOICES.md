# Technical Choices and Justifications

## 1. Detection Model Choice
- **Options Considered**: Ultralytics YOLOv8s (Small), Faster R-CNN, and a Vision-Language Model (VLM) like GPT-4o for zero-shot zone classification.
- **What AI Suggested**: The AI heavily suggested routing raw frames to a VLM (GPT-4o) to handle staff detection and zone classification dynamically via zero-shot prompts (e.g., `"Is the person in the bounding box standing in a billing queue?"`).
- **What I Chose and Why**: I chose **YOLOv8s** for bounding box detection, completely rejecting the VLM suggestion for zone classification. While a VLM would have been extremely easy to implement, it incurs a catastrophic 3–5 second latency penalty *per frame*, making video streaming impossible. Furthermore, VLM API costs for a 20-minute CCTV clip at 30 FPS would be astronomically expensive. Instead, I built a pure mathematical solution: I used YOLOv8s (which runs at near real-time speeds on CPU) and paired it with the `shapely` library to perform high-speed geometric polygon intersection (point-in-polygon) to accurately classify which zone a customer was standing in, entirely offline and for free.

## 2. Event Schema Design Rationale
- **Options Considered**: A strictly enforced unified schema (rejecting bad data) vs. a loose, completely unstructured NoSQL payload.
- **What AI Suggested**: The AI suggested using a strict Pydantic model (`class Event(BaseModel)`) that instantly throws a `422 Unprocessable Entity` if any edge camera uploads slightly malformed data (e.g., using the key `id_token` instead of `visitor_id`).
- **What I Chose and Why**: I chose a **Chaotic Schema Adapter (Pre-Validation Coercion)**. In the real world, different branches use different IoT firmwares, meaning payload keys will inevitably mismatch. Instead of outright rejecting them like the AI suggested, I implemented Pydantic's `@field_validator(mode='before')` to intercept the raw payload *before* strict validation. This allowed the backend to dynamically sniff for disorganized keys like `event_time` or `timestamp` and forcefully normalize them into the canonical schema. This ensures the database stays perfectly structured while remaining incredibly resilient to messy real-world edge devices.

## 3. API Architecture Choice
- **Options Considered**: Synchronous WSGI Framework (Flask/Django) vs. Asynchronous ASGI Framework (FastAPI + AsyncPG).
- **What AI Suggested**: The AI suggested FastAPI paired with `asyncpg` to leverage non-blocking async/await loops.
- **What I Chose and Why**: **Agreed and implemented.** I chose FastAPI explicitly because of the massive telemetry throughput. When simulating a live IoT environment (via `stream_all.py`), the backend is bombarded with dozens of POST requests per second (dwell times, zone entries, exits). A synchronous framework like Flask would tie up OS threads and choke under the simulated load. FastAPI's asynchronous event loop, coupled with asynchronous database sessions, allows the backend to ingest massive concurrent bursts of JSON traffic without thread starvation or dropping telemetry.
