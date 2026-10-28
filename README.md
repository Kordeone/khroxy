# KHROXY

KHROXY is an independent Linux proxy manager: a compact terminal UI, private per-application proxies, subscriptions, optional background supervision, health testing and automatic Fastest selection. It uses supported proxy engines directly. XRAT was a documentation-only functional reference; XRAT, Windscribe and external controllers are not runtime dependencies.

The original btop-like terminal identity is preserved: **Profiles, Applications, Subscriptions, Logs**, keyboard navigation and the dedicated **`?` help screen**. New capabilities use CLI commands rather than redesigning the interface.


## Choose an installation

**Native Linux is the primary product.** It manages local applications without a container or host-wide VPN. The optional AI workspace adds Node.js/npm/npx/Codex in a container; its live Debian firewall acceptance remains separate and unverified. Historical Windscribe experiments remain in Git history and `windscribe/`, outside standalone release artifacts.

You need Linux, Python 3.10 or newer, `curl`, and at least one trusted compatible proxy engine. Xray is the original default; V2Ray and sing-box are optional alternatives. Obtain binaries from their official releases, verify the upstream checksums, and choose the matching CPU architecture. KHROXY does not download or install system packages automatically. Tests currently cover Linux amd64 with the versions in [ENGINE_LOCK.json](ENGINE_LOCK.json); the native installer also recognizes arm64 ELF binaries, without claiming arm64 release acceptance.

From a verified source archive or checkout:

```sh
./install.sh
```

Choose **A: Native Linux**. For an explicit plan and installation:

```sh
./install.sh --mode native --xray /absolute/path/to/trusted/xray --dry-run
./install.sh --mode native --xray /absolute/path/to/trusted/xray
export PATH="$HOME/.local/bin:$PATH"
khroxy --version
khroxy
```

Use `--proxy-engine sing-box --engine-binary /absolute/path/to/sing-box` or the equivalent V2Ray options to install with a different engine. Dependencies remain optional: a sing-box installation does not require Xray. The installer preserves existing profiles, subscriptions and settings; it asks explicitly before enabling user services. Noninteractive installation without approval writes owned files/units but starts no service. Do not use sudo.

If enabling later, first import a usable profile and configure an application. Then deliberately run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now khroxy.service khroxy-health.service
```

These commands affect only your user units. No firewall, SSH, DNS, desktop, NetworkManager or other container is changed. No lingering/reboot service setting is enabled automatically. Without a usable proxy configuration, an application remains offline rather than connecting directly.

## First daily workflow

```sh
khroxy import my-profile
khroxy app-add browser --profile @active --command "firefox"
khroxy select my-profile
khroxy install
khroxy run browser
```

Paste the share link at the hidden prompt; do not place credentials in arguments or shell history. Use an executable installed on your machine for `--command`. Applications must honor HTTP proxy settings; applications that ignore them are outside KHROXY's protection. `khroxy service-env browser` shows proxy environment assignments for an application. Existing `shell-enable`, `shell-init bash` and `shell-disable` commands provide explicit optional Bash launchers.

Open `khroxy` for the original four screens. Press `?` for actual keybindings; press `q` to exit. Closing the TUI does not terminate an intentionally supervised background proxy. With the optional supervisor:

```sh
khroxy daemon start
khroxy daemon proxy-start
khroxy daemon status
khroxy daemon proxy-stop
khroxy daemon stop
```

`daemon stop` waits for owned shutdown completion. `--keep-runtime` deliberately preserves the engine for later adoption. Stop the legacy user proxy before first enabling supervisor mode; KHROXY refuses competing ownership. `khroxy daemon service` previews a rootless unit; `--write` prepares it without starting it. See [supervisor ownership/recovery](docs/SUPERVISOR.md).

## Profiles, subscriptions and engines

Existing VLESS, VMess, Trojan and Shadowsocks links remain supported. HTTP/HTTPS and SOCKS5 upstreams, plus Hysteria2 through sing-box, are available. Plaintext upstreams require explicit opt-in. TLS verification bypass and unrepresentable security/transport options are rejected.

```sh
khroxy import-data --file private-links.txt --source project
khroxy sub-add provider
khroxy sub-update provider
khroxy subs
khroxy engine list
khroxy engine check
khroxy engine preview
```

Supported import formats: links, multiline text, base64 lists, SIP008 and supported canonical/Xray JSON. Subscription updates preserve manual nodes and ownership shared with other sources. Existing proxy-assisted retrieval and periodic subscriptions remain available; saved URLs and authentication are hidden from logs. See [imports](docs/IMPORTS.md), [engines and protocol matrix](docs/ENGINES.md).

`engine select NAME --binary PATH` requires a stopped runtime, native validation and a compatible profile configuration. Preview output is redacted. A private native configuration requires explicit `--include-secrets --output PATH`; never publish it.

## Fastest and health testing

Native installation prepares periodic health checks; enabling the health user service starts them. The default interval is 300 seconds, configurable with `khroxy health-config --interval 300`. Manual `P`/`ping --all` remain available.

`@active` follows your manual selection. `@fastest` follows the fastest recently healthy eligible profile. A fixed named assignment stays fixed. Small latency differences do not trigger unnecessary switching: default hysteresis requires 20 ms and a 10% improvement. Failed or stale tests are not current availability. No healthy candidates means a clear failure and retained proxy routes, never automatic direct Internet.

Live Fastest uses native session-safe selection; existing streams and fixed mappings remain intact. A legacy/unavailable control API defers the change and reports it. Configuration changes that require a restart are not applied silently. Optional scheduled/failure rotation supports confirmation counts, cooldown and pinning; see [rotation](docs/ROTATION.md).

Advanced testing requires an explicit URL you are authorized to use:

```sh
khroxy test-profiles --help
khroxy results rank
khroxy rotation status
khroxy automation status
```

ICMP, TCP, HTTP, download and upload stages have configurable concurrency, timeouts, retries and payload budgets. ICMP depends on available nonprivileged ping support. Throughput budgets limit application payload, not exact packet overhead. History survives restart. Optional country/ASN data uses an offline user-supplied dataset. See [testing](docs/TESTING.md), [diagnostics](docs/DIAGNOSTICS.md).

## Local services, routing and automation

Original application HTTP ports remain unchanged. Additional loopback HTTP, SOCKS5 and AEAD Shadowsocks listeners are opt-in, with private authentication support. Ordered direct/proxy/block rules require explicit matchers; a direct rule is deliberate, not fallback. System/static DNS policies affect the engine, not the host resolver. Static DNS supports IPv4/IPv6 maps and refuses system fallback for local resolution; proxy-server addresses must be literal IPs. This does not control remote proxy DNS or applications bypassing their proxy.

See [listeners/routing/DNS](docs/NETWORKING.md), [bounded candidate scans](docs/SCANNING.md), and [JSON automation, exports and authenticated local API](docs/AUTOMATION.md). API startup is opt-in, authentication covers reads and writes, and public binding is refused. No desktop proxy setting is changed automatically.

```sh
khroxy automation profiles --limit 100 --offset 0
khroxy completion bash
khroxy manual > khroxy.1
man ./khroxy.1
```

## Optional Docker or rootless Podman AI workspace

The standalone application does not need this container. The installer offers **B: AI Container**, asks for an existing projects directory and offers **no Caveman**, **essentials** (Caveman/Megacave/Ultracave modes), or **full suite**. Default: no third-party skills/hooks. SessionStart hooks and a third-party default mode are not activated silently.

```sh
./install.sh --mode container --engine podman --projects /absolute/path/to/projects --dry-run
./install.sh --mode container --engine podman --projects /absolute/path/to/projects
```

The engine must already be usable without sudo. Podman must be rootless. Docker must use a local Linux Unix socket; it may still use a rootful daemon, so prefer rootless Podman. Docker builds additionally require an explicit bridge-reachable `--docker-build-proxy http://HOST:PORT`; never expose an unrestricted host proxy to satisfy that requirement. Podman build forwarding uses the existing localhost-only native proxy. Neither mode falls back to direct build networking when its proxy is unavailable.

Projects mount read/write at `/workspace`; `khroxy_config` persists manager profiles/settings and `khroxy_agent_home` persists Codex authentication, application settings and selected skills. Rebuilding does not rename or delete these volumes. Project contents are never recursively chowned. Mount only the intended projects directory, never your entire home, SSH/authentication directories or host engine socket.

Everyday workflow:

```sh
./workspace.sh shell
cd /workspace/project
codex
```

Use `./workspace.sh tui` for the original KHROXY interface, `./workspace.sh login` for explicit login, and `status`, `logs`, `verify`, `start`, `stop` for existing-container operations. A fresh container has no working proxy until you configure one. Agent direct egress remains blocked while the proxy is offline. The host proxy is published on localhost only (`127.0.0.1:20819`).

The image root is read-only; persistent home/config mounts and `/run`/`/tmp` are writable. A network guard installs both IPv4 and IPv6 OUTPUT DROP policies inside the container namespace; independent startup and isolation checks must pass before shell access. Do not disable IPv6, remove checks, use privileged/host networking, or change the host firewall to make it start. A guard success line alone does not prove final isolation.

Caveman reconciliation runs as the persisted agent owner. `./workspace.sh skills --suite essentials` or `--suite full` explicitly reconciles the image's selected templates; `--suite none` removes unchanged managed copies. Each operation reports its suite and hook/default status. Rebuilding a selected suite updates shipped templates. No hook-activation command is provided; any existing third-party hook/config must be reviewed separately. Existing user edits are preserved or refused for review. See [container security](SECURITY.md) for limitations and the still-required live Debian acceptance. This release does not claim a live-verified container kill switch or Codex authentication flow.

## Private data, upgrades and rollback

`~/.config/khroxy` is private (`0700`); SQLite, credentials, backups and runtime configurations use `0600`. These permissions protect against other local users, not root or code running as your user. After migration, SQLite is authoritative; the exact old `state.json` and `state.json.pre-sqlite` are frozen recovery sources. Do not edit them as a second writer.

```sh
khroxy storage backup /absolute/private/path/khroxy-backup.json
khroxy storage status
```

Backups contain credentials. Keep them out of Git, tickets and shared folders. For an upgrade, take a backup, stop owned API/supervisor/proxy/health/subscription processes deliberately, verify the new source checksums and rerun the native installer. Live upgrades are refused; services are not restarted automatically. Immutable CLI/module/unit snapshots live under `~/.local/lib/khroxy/rollback-code/`; failed writes attempt code/state recovery and retain private snapshots.

`./install.sh --rollback-code SNAPSHOT_ID` restores verified code/owned units after checking that runtimes are stopped. It never overwrites the database. A downgrade to the pre-SQLite application reads the frozen legacy JSON and therefore does **not** include later v0.2.0 edits. Preserve a fresh SQLite backup, stop every writer, and follow [the rollback procedure](docs/RELEASE.md) before switching data formats. Do not delete the database or persistent volumes to make a downgrade work.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing engine or invalid configuration | `khroxy engine list`, `engine check`; choose compatible protocol/engine, verify trusted binary/version |
| No healthy profile | Inspect `health`, `results rank`, `logs`; repair proxy credentials/connectivity; no direct fallback is added |
| Fastest pending | Inspect `rotation status`; arrange an explicit maintenance stop before changing installed pools/configuration |
| Stale session or offline daemon | `daemon status`; restart owned supervisor, which validates process identity; never kill an unverified PID |
| Legacy JSON changed after migration | Stop old writers, retain both copies and backups, reconcile deliberately; do not overwrite newer SQLite state |
| Port collision | Choose unused unprivileged loopback port; KHROXY rejects app/listener/control conflicts |
| Container exits after guard success | Inspect `khroxy-startup:` stages and writable mounts; startup failure differs from firewall verification failure |
| Container isolation query/probe fails | Keep it stopped; collect sanitized diagnostics for Debian review; never weaken protection |
| Native service unavailable | Use foreground CLI/optional owned daemon; do not install a system-wide unit or enable lingering implicitly |

Raw engine logs are disabled in generated production configurations to protect private URLs and authentication. KHROXY reports sanitized failure categories; inspect only explicitly private configuration files when deeper engine debugging is required.

## Uninstall

```sh
khroxy uninstall --dry-run
khroxy uninstall
```

Default uninstall stops only verified owned processes/services, removes owned units, CLI/modules and owned Bash hooks, and preserves profiles/history/backups. Engines remain independently managed. Foreign files or unsafe symlinks cause refusal rather than deletion.

For deliberate configuration destruction, back up first, review `khroxy uninstall --purge --dry-run`, then run `khroxy uninstall --purge` and type `PURGE KHROXY`. Known generated sessions/checkpoints/backups under the configuration directory are included; unrecognized files are retained. External exports/token files and immutable code snapshots remain wherever you explicitly placed them; inspect and remove those manually only when no longer needed. Unlinking is not guaranteed secure erasure on SSDs/backups.

For the optional container, stop only `khroxy-ai`, then remove that stopped container/image deliberately using the configured engine. **Do not add a volume-removal option.** Named authentication/config volumes and host projects remain intact. Do not run `volume prune`, delete project directories, or remove unrelated containers. Remove a volume only through a separate owner-approved data-destruction decision.

## Development and release verification


No network test is described as live Internet verification when it used local fixtures. Development never changes production host networking or restarts unrelated live services.

Source lineage and normalized versions: [HISTORY.md](HISTORY.md).
