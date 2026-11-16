#!/bin/bash
# Refuse a manual test-world stage before SSH.
# This script does not connect anywhere or print the submitted confirmation.
# STAGE and CONFIRMATION are data. They are never eval'd, sourced, or executed.

set -euo pipefail

ref="${GITHUB_REF-}"
if [ "${ref}" != "refs/heads/main" ]; then
    echo "Refusing to build ${ref}. Only main is built." >&2
    exit 1
fi

stage="${STAGE-}"
case "${stage}" in
    VERIFY|BUILD_DRAFT|PUBLISH|START_1970)
        ;;
    *)
        echo "Refusing to build: the stage is not legal." >&2
        exit 1
        ;;
esac

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

if [ "${confirmation}" != "${stage}" ]; then
    echo "Refusing to build: type the selected stage to continue." >&2
    exit 1
fi
