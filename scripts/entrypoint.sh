#!/usr/bin/env bash
set -euo pipefail

# Only the long-running server needs the database bootstrapped first; one-off
# commands (pytest, ruff, makemigrations) skip it and start immediately.
case "$*" in
    *runserver*|*gunicorn*)
        # Compose gates this container on db's pg_isready healthcheck, so there
        # is no polling loop here: Postgres is already accepting connections.
        python manage.py migrate --noinput
        if [ "${DJANGO_ENV:-local}" = "local" ]; then
            python manage.py seed_local
        fi
        ;;
esac

exec "$@"
