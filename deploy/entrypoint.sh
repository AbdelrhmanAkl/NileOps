#!/bin/sh
# Prepare the database and policies (idempotent), then serve.
set -e
python -m scripts.init_db
python -m scripts.load_policies
exec python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers
