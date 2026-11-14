#!/bin/bash
# Apply reviewed Django migrations on the PythonAnywhere test database.
# Every check runs before migrate. This script does not reload the site,
# print secrets, or change any database other than the configured test name.

set -euo pipefail

checkout="${1:-/home/lecrolink/federationgridiron}"
venv="${FG_MIGRATE_VENV:-/home/lecrolink/.virtualenvs/fgvenv}"
expected_db='lecrolink$fg_test'

if [ ! -d "${checkout}/.git" ] && [ ! -f "${checkout}/.git" ]; then
    echo "Refusing to migrate: ${checkout} is not a git checkout." >&2
    exit 1
fi

cd "${checkout}"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "${branch}" != "main" ]; then
    echo "Refusing to migrate: checkout is on '${branch}', not main." >&2
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "Refusing to migrate: checkout is dirty." >&2
    exit 1
fi

export GIT_TERMINAL_PROMPT=0
git fetch --quiet --no-tags origin main
local_head="$(git rev-parse HEAD)"
remote_head="$(git rev-parse origin/main)"
if [ "${local_head}" != "${remote_head}" ]; then
    echo "Refusing to migrate: checkout ${local_head} is not origin/main ${remote_head}." >&2
    exit 1
fi

if [ ! -f "${venv}/bin/activate" ]; then
    echo "Refusing to migrate: ${venv} is not a virtualenv." >&2
    exit 1
fi

set +u
# shellcheck disable=SC1091
source "${venv}/bin/activate"
set -u

python_path="$(command -v python || true)"
if [ "${python_path}" != "${venv}/bin/python" ]; then
    echo "Refusing to migrate: python is not ${venv}/bin/python." >&2
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
if [ "${db_name}" != "${expected_db}" ]; then
    echo "Refusing to migrate: Django database name is not ${expected_db}." >&2
    exit 1
fi
echo "Database name is ${expected_db}."

echo "Migration plan:"
python manage.py migrate --plan
python manage.py migrate
echo "Migrations applied on ${expected_db}."
