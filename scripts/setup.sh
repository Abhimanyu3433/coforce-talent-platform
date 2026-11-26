#!/usr/bin/env sh
# One-time setup on macOS/Linux:   sh scripts/setup.sh
set -e
cd "$(dirname "$0")/.."
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ needed"'
command -v node >/dev/null || { echo "Node.js 18+ needed"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
[ -f data/coforce.db ] || .venv/bin/python -m data_generator.generate --out data
(cd frontend && npm install)
[ -f backend/.env ] || cp backend/.env.example backend/.env
echo "Setup done. Start the app with:  sh scripts/dev.sh"
