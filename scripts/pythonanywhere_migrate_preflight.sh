#!/bin/bash
# Refuse a manual test-database migration before SSH.
# This script does not connect anywhere, print secrets, or run migrate.
# CONFIRMATION is data. It is never eval'd, sourced, or executed.

set -euo pipefail

ref="${GITHUB_REF-}"
if [ "${ref}" != "refs/heads/main" ]; then
    echo "Refusing to migrate ${ref}. Only main is migrated." >&2
    exit 1
fi

# Remove leading and trailing U+0020 spaces only. The pattern is one literal
# space, so a tab, carriage return, newline, or other control character is
# left in place and fails the exact comparison. The refusal does not echo
# the supplied value.
confirmation="${CONFIRMATION-}"
while [ "${confirmation}" != "${confirmation# }" ]; do
    confirmation="${confirmation# }"
done
while [ "${confirmation}" != "${confirmation% }" ]; do
    confirmation="${confirmation% }"
done

if [ "${confirmation}" != "MIGRATE_FG_TEST" ]; then
    echo "Refusing to migrate: type MIGRATE_FG_TEST to continue." >&2
    exit 1
fi
