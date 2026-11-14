#!/bin/bash
# Read ssh-keyscan lines on stdin and print the line whose SHA256 fingerprint
# matches the published PythonAnywhere host key. Print nothing else.

set -euo pipefail

expected="${1:-}"
if [ -z "${expected}" ]; then
    echo "Refusing to deploy: SSH host fingerprint was not provided." >&2
    exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

while IFS= read -r line || [ -n "${line}" ]; do
    case "${line}" in
        ''|\#*) continue ;;
    esac
    printf '%s\n' "${line}" > "${tmp}"
    fingerprint="$(ssh-keygen -lf "${tmp}" -E sha256 2>/dev/null | awk '{print $2}' || true)"
    if [ "${fingerprint}" = "${expected}" ]; then
        printf '%s\n' "${line}"
        exit 0
    fi
done

echo "Refusing to deploy: SSH host key does not match the published PythonAnywhere fingerprint." >&2
exit 1
