#!/bin/sh
# Independent native Khroxy installer. No sudo, system firewall or Podman.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 "$script_dir/native_install.py" "$@"
