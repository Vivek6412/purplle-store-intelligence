#!/bin/bash

# Create directories
mkdir -p store-intelligence/app/dashboard
mkdir -p store-intelligence/alembic/versions
mkdir -p store-intelligence/data/clips
mkdir -p store-intelligence/output

# Create empty Python files
touch store-intelligence/detect.py
touch store-intelligence/tracker.py
touch store-intelligence/emit.py
touch store-intelligence/feed_api.py
touch store-intelligence/test_pipeline.py
touch store-intelligence/app/__init__.py
touch store-intelligence/app/main.py
touch store-intelligence/app/db.py
touch store-intelligence/app/models.py
touch store-intelligence/app/config.py
touch store-intelligence/app/ingestion.py
touch store-intelligence/app/metrics.py
touch store-intelligence/app/funnel.py
touch store-intelligence/app/heatmap.py
touch store-intelligence/app/anomalies.py
touch store-intelligence/app/health.py
touch store-intelligence/app/pos_loader.py
touch store-intelligence/alembic/env.py
touch store-intelligence/alembic/versions/001_initial.py
