# KHROXY v0.2.0

Independent rootless Linux proxy manager; original compact four-screen TUI and dedicated `?` help preserved. No XRAT, Windscribe, Mihomo or external controller dependency.

- Transactional SQLite with exact private legacy recovery, backups/history/lifecycle and configuration preservation.
- Bounded imports/subscriptions with ownership-aware deduplication; optional Xray, V2Ray and sing-box, including Hysteria2 through sing-box.
- Concurrent bounded health/throughput testing, persistent diagnostics/offline metadata, hysteresis/rotation/pinning and native session-safe Fastest.
- Owned-process supervisor/private IPC, recovery/restart budgets and rootless user-service preparation.
- Loopback HTTP/SOCKS/AEAD Shadowsocks, explicit direct/proxy/block rules, engine-local DNS and bounded resumable explicit-candidate scans.
- JSON automation, validated private exports, authenticated loopback API, completions/manual, safe code snapshots/rollback and deterministic artifacts.

Install: verify `SHA256SUMS`, extract the source archive, run `./install.sh`, choose Native Linux and a separately trusted supported engine. Python 3.10+, Linux and curl are required. A Python zipapp is also included; it does not bundle proxy engines.

Upgrade: take a private backup, deliberately stop owned writers/runtime, rerun the installer. No automatic session termination. See the included `docs/RELEASE.md` for tested code/legacy rollback and uninstall procedures.

Validation uses all three checksum-pinned real engines and isolated local HTTP/TLS/QUIC/process/API fixtures. Public Internet reachability, arm64, live host reboot/systemd enablement and optional Debian AI-container firewall/Codex authentication acceptance are not claimed. The optional container remains a separately reviewed workspace; Windscribe experiments are excluded. Native proxy settings do not constrain applications that bypass their proxy. Loopback ports remain accessible to local processes; private authentication is optional for extra HTTP/SOCKS listeners.

The included `SOURCE.json` identifies the tested commit and file checksums. Release assets are verified again before publication. See `docs/RELEASE.md` for validation instructions and limitations.
