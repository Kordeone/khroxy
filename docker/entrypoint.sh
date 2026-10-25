#!/bin/sh
set -eu
# Startup stops on any firewall failure. Nothing user-executed is started first.
/usr/local/bin/network-guard.sh
mkdir -p /home/khroxy/.config/khroxy
chown -R khroxy:khroxy /home/khroxy/.config/khroxy
chown agent:agent /home/agent
# Never chown or otherwise modify /workspace bind-mount contents.
gosu khroxy env HOME=/home/khroxy KHROXY_CONTAINER=1 /home/khroxy/.local/bin/khroxy init
# If a previously selected profile exists, start the proxy gateway.
# Failure leaves the workspace safely OFFLINE: no direct Internet is allowed.
if ! gosu khroxy env HOME=/home/khroxy KHROXY_CONTAINER=1 \
       /home/khroxy/.local/bin/khroxy install; then
  echo 'khroxy gateway not yet connected; network guard remains active' >&2
fi
gosu khroxy env HOME=/home/khroxy KHROXY_CONTAINER=1 \
    /usr/local/bin/scheduler.sh &
# Root is only the idle bootstrap/supervisor. AI commands use `docker compose exec -u agent`.
exec sleep infinity
