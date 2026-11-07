#!/usr/bin/env bash
# Hot-reload development: Python API on :8765 (no token) + Vite on :5173.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m pysheets --dev "$@" &
API=$!
trap 'kill $API' EXIT
npm --prefix frontend run dev
