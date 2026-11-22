#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

HOST="${H3_STUDIO_HOST:-127.0.0.1}"
PORT="${H3_STUDIO_PORT:-7860}"
echo "Starting H3 Drama Studio at http://${HOST}:${PORT}"
exec python3 "$SCRIPT_DIR/app.py"
