#!/usr/bin/env sh
# Backend (:8000) in the background + frontend (:5173):   sh scripts/dev.sh
cd "$(dirname "$0")/.."
ROOT=$(pwd)
(cd backend && "$ROOT/.venv/bin/python" -m uvicorn app.main:app --reload --port 8000) &
BACK=$!
trap 'kill $BACK' INT TERM EXIT
cd frontend && npm run dev
