#!/usr/bin/env sh
cd "$(dirname "$0")/.." && rm -rf data && .venv/bin/python -m data_generator.generate --out data
