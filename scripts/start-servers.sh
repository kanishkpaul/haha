#!/usr/bin/env bash
# Start Resume-Matcher (API :8000, web app :3000) in the background and wait for health.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RM="$ROOT/vendor/resume-matcher/apps"
LOGS="$ROOT/.logs"; mkdir -p "$LOGS"

if curl -sf http://127.0.0.1:3000/api/v1/health >/dev/null; then echo "already running"; exit 0; fi
(cd "$RM/backend" && nohup uv run app >"$LOGS/backend.log" 2>&1 &)
for _ in $(seq 60); do curl -sf http://127.0.0.1:8000/api/v1/health >/dev/null && break; sleep 2; done
(cd "$RM/frontend" && nohup npm run dev >"$LOGS/frontend.log" 2>&1 &)
for _ in $(seq 60); do curl -sf http://127.0.0.1:3000/api/v1/health >/dev/null && break; sleep 2; done
curl -s http://127.0.0.1:3000/api/v1/status; echo
