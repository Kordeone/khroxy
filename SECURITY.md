# Security status — v0.9

**Development preview; not yet approved for running untrusted AI agent workloads.**

Rootless Podman reduces dependence on a root-managed container daemon but does **not** by itself guarantee an egress kill switch. `pasta` networking, IPv4/IPv6 iptables owner matching, DNS behavior, port publication, and namespace-local privileged setup must be verified on the actual Debian host.

The installer avoids host firewall, SSH, NetworkManager, Docker, and systemd modifications, and refuses rootful Podman. Before modifying any firewall policy the guard checks that it is inside Podman and in a different network namespace from the host. Startup refuses missing capabilities/netfilter and stops on any failed direct socket test. These safeguards are **defense in depth**, not a proof of confinement.

Container AI tools have read/write access to the chosen project folder, so this mount is an **intentional security exposure**. Do not mount `/`, your entire home, secrets, SSH keys, or Podman sockets. The Codex login volume is credential-sensitive.

Do not publish `docker/xray`, `workspace-settings.json`, `~/.config/khroxy/*`, `~/.codex/*`, or the Podman named volumes. The git ignore/build-context ignore are designed to exclude these but manually verify `git status --short` before publishing.

Do not publish proxy port 20819 beyond 127.0.0.1. The HTTP proxy has no authentication.

Mandatory live acceptance tests before security approval: rootless identity; separate namespace and no host-network access; IPv4/IPv6 default-deny; no direct TCP/UDP/DNS from agent when Xray is unavailable; no loopback-to-host bypass; working proxied HTTPS/WebSocket as agent; no host SSH/firewall changes; persistent data and permission checks; startup/reboot/shutdown with failure injection; image dependency verification.
