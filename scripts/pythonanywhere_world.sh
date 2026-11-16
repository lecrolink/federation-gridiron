#!/bin/bash
# Run one fg-1970 test-world stage on the PythonAnywhere test database.
# Every check runs before the management command. This script does not
# reload the site, print secrets, or apply schema changes.

set -euo pipefail

checkout="${1:-/home/lecrolink/federationgridiron}"
stage="${2:-}"
venv="${FG_WORLD_VENV:-/home/lecrolink/.virtualenvs/fgvenv}"
expected_db='lecrolink$fg_test'
production_db='lecrolink$fg'

case "${stage}" in
    VERIFY|BUILD_DRAFT|PUBLISH|START_1970)
        ;;
    *)
        echo "Refusing to build: the stage is not legal." >&2
        exit 1
        ;;
esac

if [ ! -d "${checkout}/.git" ] && [ ! -f "${checkout}/.git" ]; then
    echo "Refusing to build: ${checkout} is not a git checkout." >&2
    exit 1
fi

cd "${checkout}"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "${branch}" != "main" ]; then
    echo "Refusing to build: checkout is on '${branch}', not main." >&2
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "Refusing to build: checkout is dirty." >&2
    exit 1
fi

export GIT_TERMINAL_PROMPT=0
git fetch --quiet --no-tags origin main
local_head="$(git rev-parse HEAD)"
remote_head="$(git rev-parse origin/main)"
if [ "${local_head}" != "${remote_head}" ]; then
    echo "Refusing to build: checkout ${local_head} is not origin/main ${remote_head}." >&2
    exit 1
fi

if [ ! -f "${venv}/bin/activate" ]; then
    echo "Refusing to build: ${venv} is not a virtualenv." >&2
    exit 1
fi

set +u
# shellcheck disable=SC1091
source "${venv}/bin/activate"
set -u

python_path="$(command -v python || true)"
if [ "${python_path}" != "${venv}/bin/python" ]; then
    echo "Refusing to build: python is not ${venv}/bin/python." >&2
    exit 1
fi

db_name="$(
    python -c 'import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "federationgridiron.settings")
import django
django.setup()
from django.conf import settings
print(settings.DATABASES["default"]["NAME"], end="")'
)"
if [ "${db_name}" = "${production_db}" ]; then
    echo "Refusing to build: the production database is not a target." >&2
    exit 1
fi
if [ "${db_name}" != "${expected_db}" ]; then
    echo "Refusing to build: Django database name is not ${expected_db}." >&2
    exit 1
fi
echo "Database name is ${expected_db}."

python manage.py build_fg_test_world --stage "${stage}"
