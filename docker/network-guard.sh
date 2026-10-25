#!/bin/sh
# Deliberately fail closed. Must run only within a private container netns.
# Never run this script on the Debian host or with network_mode: host.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Network guard must run as container root' >&2; exit 1; }
[ "${KHROXY_CONTAINER:-}" = 1 ] || { echo 'Refusing to operate outside khroxy container mode' >&2; exit 1; }
# Refuse host-network containers and direct host execution (must be Docker veth).
[ -f /.dockerenv ] && ip -o link show eth0 2>/dev/null | grep -q 'eth0@if' || {
  echo 'Refusing network guard: not a Docker private bridge namespace' >&2; exit 1;
}
# Requires iptables (nft backend), including owner-match and conntrack modules.
command -v iptables >/dev/null
command -v ip6tables >/dev/null
# Kill switch FIRST, then add strictly limited exceptions.
iptables -w -P OUTPUT DROP
ip6tables -w -P OUTPUT DROP
iptables -w -F OUTPUT
ip6tables -w -F OUTPUT
# Proxy-manager UID can contact configured upstream proxy servers, including DNS.
# AI processes are a different unprivileged UID: they never get this exception.
PUID="$(id -u khroxy)"
AUID="$(id -u agent)"
iptables -w -A OUTPUT -m owner --uid-owner "$PUID" -j ACCEPT
ip6tables -w -A OUTPUT -m owner --uid-owner "$PUID" -j ACCEPT
# Replies to accepted incoming container proxy traffic are allowed.
iptables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Prevent agent resolving through Docker's embedded DNS bypassing the tunnel.
iptables -w -A OUTPUT -m owner --uid-owner "$AUID" -d 127.0.0.11 -j REJECT
# Permit loopback access to the khroxy proxy on localhost only.
iptables -w -A OUTPUT -o lo -d 127.0.0.1/32 -j ACCEPT
ip6tables -w -A OUTPUT -o lo -d ::1/128 -j ACCEPT
# Optional approved LAN services only. Space-separated literal private IPv4:TCP-port pairs.
for target in ${KHROXY_TRUSTED_LAN_TCP:-}; do
    addr="${target%:*}"; port="${target##*:}"
    python3 - "$addr" "$port" <<'PY'
import ipaddress,sys
ip=ipaddress.ip_address(sys.argv[1]); p=int(sys.argv[2]);
rfc1918=[ipaddress.IPv4Network(x) for x in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
if (ip.version != 4 or not any(ip in net for net in rfc1918) or
    not 1 <= p <= 65535):
    raise SystemExit('Refusing non-private trusted LAN address/port')
PY
    iptables -w -A OUTPUT -m owner --uid-owner "$AUID" -d "$addr" -p tcp --dport "$port" -j ACCEPT
done
# No generic rule allowing AI UDP, public IPv4, public IPv6, or direct DNS.
echo 'khroxy network guard installed inside container namespace (public AI egress DROP)'
