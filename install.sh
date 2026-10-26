#!/bin/sh
# Native Khroxy installer entry point — initial inspection only.
# No root privileges, packages, network, or host configuration changes.
set -eu
case "${1:-}" in
  ""|--check)
    [ "$(id -u)" -ne 0 ] || { echo 'Run as normal user, not root' >&2; exit 1; }
    command -v python3 >/dev/null 2>&1 || { echo 'Python 3 is required' >&2; exit 1; }
    command -v xray >/dev/null 2>&1 || echo 'Xray not on PATH; native installer will need a separately verified dependency'
    printf '%s\n' 'Native Khroxy installer: inspection only (implementation pending).' \
      'No system changes made. Run ./khroxy --help to inspect available commands.'
    ;;
  *) echo 'Usage: ./install.sh [--check]' >&2; exit 2;;
esac
