#!/bin/bash
set -e

# Resolve the project relative to this script, including from another directory.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi
exec .venv/bin/python manage.py runserver "$@"
