#!/usr/bin/env sh
cd "$(dirname "$0")/../backend" && ../.venv/bin/python -m pytest "$@"
