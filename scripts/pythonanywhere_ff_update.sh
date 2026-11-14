#!/bin/bash
# Fast-forward one existing checkout to origin/main.
# A dirty tree or a non-fast-forward update stops the script before it
# changes the checkout. This script does not migrate, reload, or read secrets.

set -euo pipefail

checkout="${1:-/home/lecrolink/federationgridiron}"

if [ ! -d "${checkout}/.git" ] && [ ! -f "${checkout}/.git" ]; then
    echo "Refusing to deploy: ${checkout} is not a git checkout." >&2
    exit 1
fi

cd "${checkout}"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "${branch}" != "main" ]; then
    echo "Refusing to deploy: checkout is on '${branch}', not main." >&2
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "Refusing to deploy: checkout is dirty." >&2
    exit 1
fi

export GIT_TERMINAL_PROMPT=0
git fetch --no-tags origin main
git merge --ff-only FETCH_HEAD
echo "Updated main to $(git rev-parse HEAD)"
