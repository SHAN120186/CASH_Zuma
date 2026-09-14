#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -c 'import fastapi,uvicorn,sqlalchemy,defusedxml' 2>/dev/null || .venv/bin/python -m pip install -r requirements.txt
exec .venv/bin/python start.py "$@"
