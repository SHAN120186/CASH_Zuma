#!/bin/sh
set -eu
if [ "${UZGERMED_HOSTING:-}" = "render" ]; then
    exec python deploy/render_start.py
fi
python manage.py init --no-user
exec "$@"
