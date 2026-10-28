#!/bin/sh
# One interactive entrypoint; no package manager or service runs implicitly.
set -eu
exec python3 "$(dirname "$0")/installer.py" "$@"
