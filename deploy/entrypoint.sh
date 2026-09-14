#!/bin/sh
set -eu
python manage.py init --no-user
exec "$@"
