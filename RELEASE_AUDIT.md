# khroxy 0.9 release audit

**Tested locally:** Python offline unit suite, shell syntax, Python bytecode compilation, rootless setup command construction, generated mount/port policies, safe flags and refusal paths. No real Podman daemon, user namespace, netfilter or external network was available to test.

**Not verified:** `podman build` dependencies (base Node 24, npm Codex, Caveman GitHub source), Podman rootless runtime on Debian 13, firewall rule installation, DNS/UDP/IPv6 leak resistance, Xray profile routing, host port accessibility, Codex login, and the exact Linux AppArmor rules.

**Known limitation:** Other Podman containers do not automatically gain access to the rootless `pasta` container through Debian's loopback-only binding. A controlled shared network must be developed and tested separately.

**No remote changes:** No Docker/Podman host was touched. No native khroxy profiles or Codex sessions were modified.

**Policy:** do not call this a verified secure tunnel until live acceptance tests in `SECURITY.md` pass. Never disable `network-guard.sh` or switch to host networking to make it boot.
