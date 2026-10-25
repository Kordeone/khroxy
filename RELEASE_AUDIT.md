# v0.9.1 build-failure repair review

- Reproduced failure class from Debian logs: base image and apt succeeded, npm install of @openai/codex timed out fetching registry.npmjs.org during Podman build.
- Build network now forwards host khroxy HTTP port via isolated rootless pasta (`-T`); avoids `--network=host`.
- Explicit host registry probe and Podman-to-host TCP probe run before the costly build. Failure aborts instead of switching direct.
- Full offline suite: 90 tests passed; shell syntax and Python compile passed.
- Runtime `--network=pasta`, iptables policy, local-only published port, workspace mount, and four-screen TUI were not changed.
- **Not yet verified** on Debian with real Podman or remote npm/Caveman. Live runtime kill switch and DNS/IPv6 leak prevention remain unverified, as with v0.9.

# khroxy 0.9 release audit

**Tested locally:** Python offline unit suite, shell syntax, Python bytecode compilation, rootless setup command construction, generated mount/port policies, safe flags and refusal paths. No real Podman daemon, user namespace, netfilter or external network was available to test.

**Not verified:** `podman build` dependencies (base Node 24, npm Codex, Caveman GitHub source), Podman rootless runtime on Debian 13, firewall rule installation, DNS/UDP/IPv6 leak resistance, Xray profile routing, host port accessibility, Codex login, and the exact Linux AppArmor rules.

**Known limitation:** Other Podman containers do not automatically gain access to the rootless `pasta` container through Debian's loopback-only binding. A controlled shared network must be developed and tested separately.

**No remote changes:** No Docker/Podman host was touched. No native khroxy profiles or Codex sessions were modified.

**Policy:** do not call this a verified secure tunnel until live acceptance tests in `SECURITY.md` pass. Never disable `network-guard.sh` or switch to host networking to make it boot.
