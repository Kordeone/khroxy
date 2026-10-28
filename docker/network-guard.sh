#!/bin/sh
# Deliberately fail closed. Must run only within a private container netns.
# Never run this script on the Debian host or with network_mode: host.
set -eu
stage=namespace
diagnostic() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "network-guard: FAILED stage=$stage exit=$rc; no agent shell started" >&2
    echo 'network-guard: check private netns, rootless NET_ADMIN, nftables and IPv6/owner/conntrack kernel support' >&2
  fi
}
trap diagnostic EXIT
[ "$(id -u)" = 0 ] || { echo 'Network guard must run as container root' >&2; exit 1; }
[ "${KHROXY_CONTAINER:-}" = 1 ] || { echo 'Refusing to operate outside khroxy container mode' >&2; exit 1; }
# Refuse accidental host execution and a host-network Podman container.
# Setup supplies the host namespace inode from before podman run. The guard
# must compare it BEFORE changing any network policy.
[ -f /run/.containerenv ] || [ -f /.containerenv ] || [ -f /.dockerenv ] || {
  echo 'Refusing network guard: not inside a Podman or Docker container' >&2; exit 1;
}
case "${KHROXY_HOST_NETNS:-}" in
  net:\[*\]) ;;
  *) echo 'Refusing missing or invalid host network namespace fingerprint' >&2; exit 1 ;;
esac
current_ns="$(readlink /proc/self/ns/net)"
[ "$current_ns" != "$KHROXY_HOST_NETNS" ] || {
  echo 'Refusing network guard: container shares HOST network namespace' >&2; exit 1;
}
# Check a non-loopback interface exists in the private container namespace.
ip -o link show | grep -qvE '^[0-9]+: lo:' || {
  echo 'Refusing network guard: no isolated network device present' >&2; exit 1;
}
# Requires iptables (nft backend), including owner-match and conntrack modules.
command -v iptables >/dev/null
command -v ip6tables >/dev/null
echo "network-guard: private namespace confirmed; uid=$(id -u)" >&2
iptables --version >&2
ip6tables --version >&2
# Kill switch FIRST, then add strictly limited exceptions.
stage=ipv4-policy
iptables -w -P OUTPUT DROP
stage=ipv6-policy
ip6tables -w -P OUTPUT DROP
stage=flush-output
iptables -w -F OUTPUT
ip6tables -w -F OUTPUT
# Proxy-manager UID can contact configured upstream proxy servers, including DNS.
# AI processes are a different unprivileged UID: they never get this exception.
PUID="$(id -u khroxy)"
AUID="$(id -u agent)"
stage=owner-rules
iptables -w -A OUTPUT -m owner --uid-owner "$PUID" -j ACCEPT
ip6tables -w -A OUTPUT -m owner --uid-owner "$PUID" -j ACCEPT
# Replies to accepted incoming container proxy traffic are allowed.
stage=conntrack-rules
iptables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Prevent bypass via special internal DNS listeners (if configured).
stage=loopback-rules
iptables -w -A OUTPUT -m owner --uid-owner "$AUID" -d 127.0.0.11 -j REJECT
# Permit loopback access to the khroxy proxy on localhost only.
iptables -w -A OUTPUT -o lo -d 127.0.0.1/32 -j ACCEPT
ip6tables -w -A OUTPUT -o lo -d ::1/128 -j ACCEPT
# Optional approved LAN services only. Space-separated literal private IPv4:TCP-port pairs.
for target in ${KHROXY_TRUSTED_LAN_TCP:-}; do
    stage=trusted-lan
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
stage=verify-policy
iptables -w -S OUTPUT | grep -Fxq -- '-P OUTPUT DROP'
ip6tables -w -S OUTPUT | grep -Fxq -- '-P OUTPUT DROP'
# This root-owned marker is diagnostic evidence, never a replacement for rule checks.
install -m 0644 /dev/null /run/khroxy-guard-ready
echo 'network-guard: READY IPv4 and IPv6 OUTPUT DROP verified' >&2
# No generic rule allowing AI UDP, public IPv4, public IPv6, or direct DNS.
echo 'khroxy network guard installed inside container namespace (public AI egress DROP)'
