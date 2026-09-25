#!/bin/sh
set -eu
# Render sets RENDER=true and RENDER_SERVICE_ID in every service it runs, so a
# service created by hand without render.yaml still takes the cloud path instead
# of silently starting the local SQLite fallback on an ephemeral disk.
if [ "${UZGERMED_HOSTING:-}" = "render" ] || { [ "${RENDER:-}" = "true" ] && [ -n "${RENDER_SERVICE_ID:-}" ]; }; then
    exec python deploy/render_start.py
fi
python manage.py init --no-user
exec "$@"
