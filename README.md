# khroxy v0.7.1 — Audited TUI and Docker development preview

This release upgrades v0.6.2 or v0.7 **in place** without deleting profiles, subscriptions, application assignments, or previous logs. It keeps the compact btop-like terminal design, but separates the old combined dashboard into four full-screen views. No host firewall, Docker daemon, network service, or AI tools are changed simply by installing this CLI.

## What's implemented and locally tested

- Four independent views: **1 Profiles**, **2 Applications**, **3 Subscriptions**, **4 Logs**. `Tab` cycles forward, `Shift+Tab` / Left cycles backward, `?` opens the scrollable keybindings screen. The button-like action footer is absent.
- Profiles show protocol, proxy endpoint address and port (without secrets), network type, TLS/REALITY security, measured HTTPS response time, subscription ownership, and apps using the profile.
- **`*` marks the selected active route**, independent of `>` (cursor). On the Profiles view, press **Enter** to select a fixed active route, or **g** to select fastest mode from recent successful tests. Selecting does not modify unrelated installed binaries.
- **p** tests the highlighted profile; **P** tests all profiles asynchronously. The latency figure is time to make an HTTPS request *through* a temporary Xray connection, not ICMP ping. Tests do not require an application assignment or a running khroxy service. The previous private proxy profile/session is not touched.
- App assignments support a **fixed profile name**, `@active` (manually selected), or `@fastest` (latest measured working). New runtime configurations contain **no direct/freedom outbound fallback**. If a symbolic route has no selected profile, configuration validation fails rather than routing directly.
- The fastest choice is updated after testing. A service restart is requested only if a running app's effective route actually changes. The currently selected **gateway mode** is preserved during periodic tests: background probing does not change a deliberately chosen fixed mode to Fastest.
- Logs have their own scrollable, full-screen view. Sensitive subscription URLs, Xray UUIDs and passwords are not displayed in logs.
- A **single-container Docker development preview** with a configurable host-local proxy port, Xray runtime, Codex and Kilo CLIs, and an unprivileged AI shell is included. The container scripts enforce OUTPUT DROP for AI traffic, with narrowly scoped exceptions; no direct public network fallback. Docker testing on Debian is **not complete**; do not assume the kill switch is proven until acceptance tests pass.

## Upgrade existing host CLI (safe; no sudo)

Extract the ZIP and in the resulting `khroxy-v0.7.1` directory:

```sh
python3 -m unittest discover -p 'test_*.py' -q
install -Dm700 khroxy ~/.local/bin/khroxy
hash -r
khroxy
```

This does **not** uninstall v0.6.2, recreate your Xray profile information, touch `codex-xray`, or start Docker. If the host's `khroxy.service` was already running, existing processes continue; reload is only performed if you change route configuration.

### Keybindings

- `1` Profiles, `2` Applications, `3` Subscriptions, `4` Logs. `Tab` next view, `Shift+Tab` / Left previous view, `b` toggles Profiles/Subscriptions for muscle memory.
- `↑/↓`, `j/k`: move cursor in lists or scroll logs; `PgUp/PgDn`, `Home/End`: jump.
- **Profiles**: `Enter` select active `*`; `p` test selected; `P` test all; `g` activate fastest; `a` import one link; `n` direct subscription; `v` subscription through selected profile; `d` delete with confirmation.
- **Applications**: `a` add; `s` reassign (fixed name, `@active`, `@fastest`); `t` check service listener's outbound response; `r` launch; `d` delete.
- **Subscriptions**: `a` add, `u` fetch, `f` fetch route, `e` auto-update, `d` remove.
- **Logs**: scroll. `?` opens detailed keybindings; `Esc`, `?`, Enter, or `q` closes help without exiting khroxy.
- Global: `i` service install/start, `o` stop, `w` Bash integration, `x` uninstall/cleanup preview, `q` quit.

### CLI alternatives

```sh
khroxy profiles
khroxy select joyboymain2          # choose * active route
khroxy ping joyboymain2            # one HTTPS latency test
khroxy ping --all                  # test all and update best recorded profile
khroxy fastest                     # choose fastest operating mode using recent tests
khroxy fastest --test              # test all, then choose Fastest mode
khroxy app-add kilo --profile @fastest --command kilo
khroxy switch kilo @active
khroxy apps
khroxy sub-update joy
khroxy logs
```

`@active` and `@fastest` are symbolic assignments, not real profile names. They resolve to a saved named Xray profile when runtime configuration is generated. The temporary listener for a latency test starts only long enough to measure the connection. An app that ignores HTTP(S)_PROXY env vars still cannot be forced through this host CLI in v0.7.

## Single Docker container — development preview (not deployed)

The container is an additional feature, not a replacement for the host TUI. One container contains separate Linux users: **khroxy**, permitted to initiate upstream proxy connections and manage a private Xray service, and **agent**, used for Codex/Kilo/Git/other development commands with restricted egress. No Docker socket mount, no host network mode, no `--privileged`, no host nftables/iptables changes by khroxy scripts. The container has a read-only root filesystem and only its named volumes plus `/mnt/storage/Projects` are writable (subject to file ownership).

### Intended network behavior

| Component | Access |
|---|---|
| AI (`agent`) to `127.0.0.1:20819` | Allowed: khroxy HTTP proxy |
| AI direct public IPv4/IPv6 / UDP / DNS | Denied in the container's OUTPUT policy |
| AI to explicitly trusted IPv4:TCP-port LAN service | Allowed only if specifically listed in `KHROXY_TRUSTED_LAN_TCP` |
| Xray / khroxy user to upstream proxy servers | Allowed so it can establish a tunnel |
| Host Debian -> gateway proxy | `127.0.0.1:20819` published (not all interfaces) |
| Other approved Docker containers -> gateway | Attach to `khroxy_private`; use `khroxy:20819` |
| No profile selected, Xray stopped, or proxy fails | No direct public fallback for the AI user |

**Important:** v0.7 routes proxy-aware apps through HTTP(S) proxy environment variables and blocks direct egress. Apps that ignore proxy settings will **fail to connect**; they are not yet transparently redirected. Transparent TCP/UDP gatewaying remains a later milestone requiring a different testable routing mechanism. The Docker image has **not** been built/run in this environment because Docker is unavailable. A source/static firewall-policy check is **not** proof of leak prevention. Docker Compose may create or modify rules in the *host* firewall even though khroxy's network guard is namespace-local. On khomelab, Docker was previously masked and the firewall baseline is protected: **do not unmask or enable Docker as part of this preview without an approved host change and SSH continuity plan**.

### Preview build procedure, for a safe Docker-enabled staging host

Prerequisite: a trusted, working Xray binary for the image's CPU architecture. Docker building requires a binary **you provide**, so we do not download an unverified executable during the build. These steps are **not** a suggestion to deploy on your production Debian host yet.

```sh
cd khroxy-v0.7.1
cp ~/.local/bin/xray docker/xray
chmod 755 docker/xray
cp docker/.env.example .env
# Edit .env and set AGENT_UID to `id -u` on the projects owner.
docker compose config
# After review and explicit deployment approval:
docker compose build
docker compose up -d
```

Example: open the **same TUI** inside the container and select a profile, then press `i` to activate its gateway. The container has its own named-volume profile database, initially empty; it does not automatically copy your existing host credentials.

```sh
docker compose exec -u khroxy -e HOME=/home/khroxy khroxy khroxy
docker compose exec -u agent -e HOME=/home/agent khroxy bash
```

Within the AI shell, `codex` and `kilo` are installed during image build, and `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` point to the container's localhost gateway. The image uses Node 24 for these CLIs. Do not `npm update` or install packages as root in the running container; rebuild the image to update tools. Currently package versions are resolved at build time (not strictly pinned), so record `npm ls -g --depth=0` and pin exact versions before production use. Any credentials used by the AI CLIs live in the separate agent home volume; do not mount host `~/.ssh` or cloud credential directories without deliberate approval.

On the Docker host, the exposed proxy endpoint is `http://127.0.0.1:20819`. Other containers can access it via `http://khroxy:20819` **only** if attached to the private Docker bridge network; membership is the access-control boundary, since the gateway HTTP proxy has no user/password authentication. Docker's documentation cautions that localhost-published ports on versions older than 28.0.0 could be reachable from the same L2 segment: confirm the installed Docker Engine version and isolation before relying on this binding.

### Must-pass live safety tests (NOT performed here)

1. Check production SSH and khomelab's nftables/preservation health before enabling Docker on any host; Docker's host networking can alter firewall rules.
2. Start the container *with zero profiles*. Confirm its network guard initializes, gateway stays offline, and direct `curl --noproxy '*'` by UID `agent` to public IP fails.
3. Configure a verified profile with the TUI, press `i`, and verify an HTTP 204 through `http://127.0.0.1:20819` from both the agent and Debian host.
4. Terminate Xray deliberately; confirm that direct public IPv4, IPv6, DNS UDP, and all proxy-ignoring tools remain blocked. Test the approved LAN allowlist separately by exact IP and port.
5. Test a stopped container, restart, destroyed/rebuilt container, changed profile, deleted subscription, and failed subscription refresh. Confirm profile persistence, no direct fallback, and no unintended host firewall modifications.
6. Verify no unexpected exposure from other Docker networks or host network namespaces.

If the kernel/netfilter capability or guard setup fails, container startup **must abort**, not start the AI tools with unrestricted egress. The scripts install DROP policy first. These security invariants are unverified until actual namespace and packet tests pass.

## Test results and limits

Offline: **72 automated tests** passed, including existing v0.6.2 regressions, four-screen navigation, active marker, HTTPS proxy timing, fixed/@active/@fastest routing, and Docker Compose/firewall policy structure. A non-root real pseudo-terminal opened all four views and the `?` help screen and exited normally. Docker daemon integration, Xray connectivity with the private live subscription, and actual firewall leak tests are **not available in this environment**.

## Removal of old Codex Xray service

The existing `retire-codex-xray.sh` script remains included, but it is **not** invoked automatically. It recognizes the original supported `xray-status -> xray-switch` symlink, preserves the shared Xray binary, and requires explicit confirmation to remove the old Codex-specific service/scripts. Do this only after testing the replacement proxy for your actual AI CLI.

```sh
./retire-codex-xray.sh --dry-run
# Later, if approved:
./retire-codex-xray.sh --apply
```

Host uninstall/purge remains `khroxy uninstall --purge --dry-run` to review, followed by an explicit user confirmation for any destructive action. Never remove active profiles and user credentials merely to upgrade khroxy.

## v0.7.1 audit fixes and candid limitations

- Corrected a keybinding collision: `g` now activates Fastest mode on the Profiles screen; it previously jumped to the final row, so the documented action was unreachable.
- In subscription `any` fetch mode, unresolved symbolic app routes no longer prevent trying otherwise valid profiles.
- The **host** CLI does not run periodic latency probes automatically. It follows the fastest recently measured route, refreshed using `p`/`P` or `khroxy ping --all`. The experimental Docker scheduler runs checks every 10 minutes; automatic host polling is a future feature.
- An Xray config syntax check or a successful HTTP 204 test is not equivalent to proving safe system-wide network isolation or complete compatibility with every AI provider endpoint.
- The Docker image installs unpinned Codex/Kilo versions at build time. Pin and audit those versions before a reproducible production release.
- The combined Docker design blocks direct AI egress but requires actual kernel-level and host firewall tests before use as a kill switch. **Do not start Docker on khomelab solely to test this ZIP.**
- Repository hygiene, security status, and publishing steps: see `RELEASE_AUDIT.md`, `SECURITY.md`, and `PUBLISH.md`.
