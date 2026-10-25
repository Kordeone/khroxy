# khroxy v0.7.1 — source review and validation status

**Scope:** audit of the packaged Python CLI/TUI, subscription routes, Docker development preview, retire helper, and Git publication. This review does not claim formal verification or a production security audit.

## Fixed in this release

1. **Functional / high:** `g` action was unreachable in the Profiles screen because it was processed earlier as a jump-to-last-row navigation key. Fixed by reserving `g` for Fastest and keeping ordinary Home/End keys for navigation. Test: `TUIActionBindings.test_g_activates_fastest_instead_of_scrolling_to_end`.
2. **Reliability / medium:** subscription fetch mode `any` could crash on a stale application assignment using unresolved `@active` or `@fastest`, skipping all valid candidate profiles. Fixed and tested for fallback behavior.
3. **Publication / high:** v0.7 had no repository-level `.gitignore`, CI, or security deployment status. Added a git exclusion policy, offline GitHub Actions checks, and security/deployment notes. Secrets and Xray binary are not included in the packaged source.

## Verified here

- 72 Python offline unit/regression tests pass (`python3 -m unittest discover -p 'test_*.py' -q`).
- Python source compiles (`python3 -m py_compile khroxy`).
- Shell scripts pass syntax checks (`bash -n` and `sh -n`).
- A non-root pseudo-terminal smoke test opened all four screens and the `?` help overlay, then quit with exit code 0.
- No user host configuration, systemd services, Docker daemon, nftables rules, Codex login, or remote server has been modified by these checks.

## Still NOT verified — deployment blockers

- **Docker isolation and kill switch:** no Docker daemon available here; runtime firewall behavior, IPv4/IPv6 DNS leak resistance and an attacker-controlled proxy-ignoring tool have not been tested live. Static rule inspection does not prove isolation.
- **Host firewall:** Docker bridge setup can change host nftables/iptables behavior. The target Debian khomelab host has protected firewall/SSH continuity requirements and Docker was previously masked. Do not unmask Docker, start Compose, or install host firewall rules without a separate owner-reviewed test procedure.
- **Fastest health monitoring:** the host user service does not yet have a periodic probe scheduler. `@fastest` follows recent measurements only, rather than continuous automatic best-route switching on Debian.
- **Provider/network tests:** untrusted real endpoints, current private subscriptions, WebSockets, real Codex auth, live failover, and true network throughput have not been validated here.
- **Dependency reproducibility:** Docker image currently installs Codex/Kilo npm packages without exact pinned versions and copies a separately supplied Xray executable. This is a development preview, not a reproducible production artifact.
- **Proxy port authentication:** the Docker shared HTTP proxy relies on loopback publication plus explicit trusted Docker bridge membership. It does not support authenticated access; never expose it publicly.

## Release decision

**Host CLI/TUI:** appropriate for local/manual testing with existing state backed up, not certified bug-free. 
**Docker:** staging prototype only, not approved for khomelab deployment.
**GitHub:** source ready to place in an initially private repository. Publishing the repository itself does not deploy or test Docker.
