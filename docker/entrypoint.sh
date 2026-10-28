#!/bin/sh
set -eu
umask 077
stage=network-guard
startup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "khroxy-startup: FAILED stage=$stage exit=$rc; isolation verification not reached" >&2
  fi
}
trap startup_failure EXIT
# Startup stops on any firewall failure. Nothing user-executed is started first.
/usr/local/bin/network-guard.sh
stage=volume-ownership
# These mountpoints and their traversable parents exist in the read-only image.
# Bootstrap root has CHOWN but no DAC_OVERRIDE/FOWNER; never mkdir a home here.
chown khroxy:khroxy /home/khroxy/.config/khroxy
chown agent:agent /home/agent
stage=runtime-paths
gosu agent chmod 0700 /home/agent
gosu khroxy chmod 0700 /home/khroxy/.config/khroxy
gosu khroxy python3 /usr/local/bin/runtime-paths.py khroxy
gosu agent python3 /usr/local/bin/runtime-paths.py agent
stage=skills
gosu agent env HOME=/home/agent python3 /usr/local/bin/caveman.py --suite "${KHROXY_CAVEMAN_SUITE:-none}"
# Never chown or otherwise modify /workspace bind-mount contents.
stage=manager-init
gosu khroxy env HOME=/home/khroxy KHROXY_CONTAINER=1 /home/khroxy/.local/bin/khroxy init
# If a previously selected profile exists, start the proxy gateway.
# Failure leaves the workspace safely OFFLINE: no direct Internet is allowed.
if ! gosu khroxy env HOME=/home/khroxy KHROXY_CONTAINER=1 \
       /home/khroxy/.local/bin/khroxy install; then
  echo 'khroxy gateway not yet connected; network guard remains active' >&2
fi
gosu khroxy env HOME=/home/khroxy KHROXY_CONTAINER=1 \
    /usr/local/bin/scheduler.sh &
stage=ready
install -m 0644 /dev/null /run/khroxy-startup-ready
echo 'khroxy-startup: READY; read-only image and writable persistent runtime verified' >&2
# Root is only the idle bootstrap/supervisor. AI commands use `workspace.sh codex/shell`.
exec sleep infinity
