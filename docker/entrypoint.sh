#!/bin/sh
set -e
if [ ! -f "$COFORCE_DB" ]; then
  echo "No database at $COFORCE_DB — generating synthetic data..."
  python -m data_generator.generate --out "$(dirname "$COFORCE_DB")"
fi
cd /app/backend
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
