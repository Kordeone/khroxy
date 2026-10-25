#!/bin/sh
set -eu
# Run as khroxy user. Only periodic background maintenance, not AI code.
while :; do
  sleep 600
  # Lock ensures long profile tests never overlap the next maintenance cycle.
  (
    flock -n 9 || exit 0
    HOME=/home/khroxy KHROXY_CONTAINER=1 khroxy sub-update --due >/dev/null 2>&1 || :
    HOME=/home/khroxy KHROXY_CONTAINER=1 khroxy ping --all >/dev/null 2>&1 || :
  ) 9>/home/khroxy/.config/khroxy/.schedule-lock
done
