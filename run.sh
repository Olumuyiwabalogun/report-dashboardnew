#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements-dev.txt
export DEMO_MODE=1
( sleep 2; xdg-open http://127.0.0.1:5000 2>/dev/null || open http://127.0.0.1:5000 2>/dev/null ) &
echo "Dashboard running at http://127.0.0.1:5000  (press Ctrl+C to stop)"
flask --app app run
