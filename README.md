# KHROXY — standalone per-application proxy manager

khroxy gives each application a local HTTP proxy backed by a saved Xray profile. Its terminal interface has four screens: Profiles, Applications, Subscriptions, and Logs. You can use it directly on Linux or inside a separate AI container.

The v0.2.0.dev5 development target is the standalone CLI and original four-screen TUI. XRAT is a functional reference only; XRAT, Windscribe and Mihomo are not dependencies. Experimental VPN work remains under `windscribe/` and is excluded from standalone release artifacts. Progress and release gates are tracked in [docs/TASKS.md](docs/TASKS.md). Container isolation still needs acceptance testing on the actual Debian host. A successful image build does not prove that its firewall works. Nothing in installation changes the host firewall or SSH.

## Before you begin

Use your normal Linux account, not `sudo`. You need Python 3, curl, and a trusted supported Linux proxy engine matching your CPU. Xray remains the default; a native install can instead use `--proxy-engine sing-box --engine-binary /trusted/path/sing-box` (or V2Ray). See [engine compatibility and verified integration](docs/ENGINES.md). Put Xray at `~/.local/bin/xray`, or supply `--xray /absolute/path/to/xray`. The installer checks its ELF architecture; this is not a signature or authenticity check. Obtain and verify Xray separately.

For containers, also install a local Linux Docker engine or rootless Podman with pasta support. The installer does not install packages, configure subordinate user IDs, modify daemon settings, or enable lingering. Podman must report `host.security.rootless: true` in `podman info --format json`. Docker access must work without `sudo` through a local Unix socket. Docker daemon access usually grants substantial host privileges.

Run commands below from this repository's directory. Start with a preview:

```sh
./install.sh --mode native --dry-run
# Or choose container mode and an existing directory you own:
./install.sh --mode container --projects /srv/work/projects --dry-run
```

Dry runs read configuration and validate paths; they do not write files, build images, start containers, or call systemd. Running `./install.sh` without arguments offers A) Native Linux and B) AI Container.

## A. Native Linux

```sh
./install.sh --mode native
```

This installs `~/.local/bin/khroxy`, copies a trusted Xray only if absent, and prepares two user units: `khroxy.service` and `khroxy-health.service`. It retains profiles, application assignments, subscriptions, unknown configuration fields, and existing settings. An updated CLI gets one rollback copy at `~/.local/bin/khroxy.previous`. Foreign binaries, foreign units, and symlink destinations are refused.

Installation asks explicitly before enabling or starting user services. Declining leaves the files prepared. Enable them later with:

```sh
systemctl --user daemon-reload
systemctl --user enable --now khroxy.service khroxy-health.service
```

The proxy unit waits until a generated runtime configuration exists. User services normally run during your login session; running after logout requires a separate, owner-approved Linux lingering configuration. Installation never changes it.

If `khroxy` is not found, add `~/.local/bin` to your shell's PATH, for example for the current shell:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Import private share links at the hidden prompt, then configure an application:

```sh
khroxy import main
khroxy import backup
khroxy health
khroxy app-add codex --profile @fastest --command codex
khroxy install
khroxy run codex
```

Profiles are not bundled. You need working credentials from your proxy provider. Do not paste them into public issues or command arguments. Native khroxy does not install Node.js or Codex; install the applications you intend to run separately.

Open the TUI with `khroxy tui` (or `khroxy`). Use `1`–`4` or Tab/Shift+Tab to change screens, arrows or `j`/`k` to move, `?` for scrollable keyboard help, and `q` to quit. Existing navigation and actions remain available. In Profiles, Enter selects an active profile, `g` selects Fastest, `p` tests one profile, and `P` tests all manually. In Applications, `s` changes the assignment. Manual configuration changes can restart the proxy: finish important sessions first.

## Data storage

New installs use private local SQLite. Existing JSON remains readable; native upgrades migrate with an exact private backup. See [storage and migration](docs/STORAGE.md) before backup, restore or rollback.

## Automatic health checks and Fastest

The native health service tests every saved profile through a temporary Xray proxy. Latency measures an HTTPS request through that profile, including connection setup. It is not an ICMP ping. No manual `P` or `ping --all` is needed for periodic tests.

Default interval: 300 seconds after each cycle. Default switching threshold: at least **20 ms and 10%** improvement over the current healthy profile. A failed current profile can be replaced immediately by a healthy candidate. Profiles deleted or edited during testing cannot inherit obsolete results.

```sh
khroxy health-config --interval 120 --min-improvement-ms 25 --min-improvement-ratio 0.15
khroxy health
khroxy status
khroxy logs
journalctl --user -u khroxy-health.service -n 60
```

A running watcher reads interval changes for its next cycle. The interval must be 30–86400 seconds. `./install.sh --mode native --health-interval 120` also sets it.

Assignments have different meanings:

| Assignment | Behavior |
| --- | --- |
| `main` | Always uses that fixed profile; health checks do not change it. |
| `@active` | Uses the profile you explicitly select with Enter or `khroxy select main`. |
| `@fastest` | Uses the fastest healthy candidate, subject to the switching threshold. |

New native installations enable session-safe Fastest routing. A private Unix socket inside the user-only khroxy configuration directory exposes only Xray's RoutingService. Health checks change the balancer target for **new connections**; existing connections continue through their original outbound. They never restart Xray, remove outbounds, or kill application processes.

Older running gateways, unsupported Xray API versions, API failures, and newly added/edited outbound definitions may require maintenance. Status reports `Fastest pending`; no forced restart occurs. After finishing application sessions, stop the proxy, run a health cycle, then start it again:

```sh
systemctl --user stop khroxy.service
khroxy health
khroxy install
```

This also adopts session-safe routing after upgrading an existing installation. If all profiles fail, or none exist, checks report failure, retain existing assignments, and never create a direct Internet route. Existing failed routes may remain unavailable until a profile recovers. Check `khroxy status` and the health service journal.

## B. AI container: rootless Podman or Docker

```sh
./install.sh --mode container
```

Choose the engine, projects directory, and Caveman skill set. This mode stages a local Xray binary, builds an image when requested, and separately asks before starting it. You can separate the steps:

```sh
./install.sh --mode container --engine podman \
  --projects /srv/work/projects --caveman essentials --configure-only
./install.sh --mode container --engine podman \
  --projects /srv/work/projects --caveman essentials --build
./install.sh --mode container --engine podman \
  --projects /srv/work/projects --caveman essentials --start
```

`--start` builds, then asks for explicit start confirmation. Repeated configuration preserves unknown settings. Existing containers are never automatically removed or replaced. `python3 setup.py` remains available for older workflows. `compose.yaml` is an advanced reference; use the unified installer for the supported workflow and its isolation gate.

The image contains Node.js 24, npm, npx, Python, curl, Git, pinned Codex 0.164.0, khroxy, and the supplied Xray. Image dependencies are installed only during a build. The image tag is `localhost/khroxy-ai:0.2.0.dev5`; an existing running container retains its original image until you explicitly recreate it.

### Build downloads

Podman builds use an existing native khroxy HTTP proxy, normally `127.0.0.1:20820`. Configure and test that native application proxy first (`khroxy test codex`). Use `--build-proxy-port PORT` to change the port. The build checks npm access and pasta TCP forwarding before package installation. It never selects host networking or a direct fallback.

Docker's bridge cannot normally reach a host service bound only to loopback. Docker builds therefore require an explicitly configured proxy reachable from the build bridge:

```sh
./install.sh --mode container --engine docker \
  --projects /srv/work/projects --caveman none \
  --docker-build-proxy http://proxy.example.internal:3128 --build
```

The example hostname is a placeholder. Supply a real, reviewed HTTP proxy without credentials in its URL. `host.docker.internal` maps to the host gateway; it does not make a loopback-only proxy reachable. The installer will not widen any host listener or firewall rule. If no suitable build proxy exists, stop for host-specific review. Package and base-image downloads depend on the engine's own registry/network configuration; this is separate from runtime confinement.

### First use and everyday workflow

After a verified start, configure profiles **inside** the container; native credentials are not copied automatically:

```sh
./workspace.sh tui
```

In Profiles press `a` to import a private share link, Enter to select it, then `i` to start the container gateway. Alternatively select Fastest after testing. The container scheduler runs periodic health checks; it retains legacy routing unless explicitly migrated. The container remains offline until a working profile is configured.

Authenticate and work as the unprivileged agent user:

```sh
./workspace.sh login
./workspace.sh shell
cd /workspace/project
codex
```

That is the daily sequence: `./workspace.sh shell` → `cd /workspace/project` → `codex`. Finish with `exit` to return to the host. `./workspace.sh codex` launches directly in `/workspace`; use a shell when you need a particular project directory.

```sh
./workspace.sh status
./workspace.sh logs
./workspace.sh verify
./workspace.sh stop
./workspace.sh start
```

Shell, login, Codex, and TUI access run isolation smoke checks first. Failed start verification stops the container and preserves its volumes. Directly running `docker exec` or `podman exec` avoids this helper gate; the engine owner is trusted.

### Project files, credentials, and proxy ports

| Location | Purpose and persistence |
| --- | --- |
| Selected host projects directory | Read/write bind mount at `/workspace`; edits are real host edits. |
| `khroxy_agent_home` named volume | `/home/agent`; Codex authentication, application settings, and managed skills survive rebuilds/recreation. |
| `khroxy_config` named volume | `/home/khroxy/.config/khroxy`; separate container profiles and runtime settings. |
| `workspace-settings.json` | Private local installer choices; ignored by Git. |
| `docker/xray` | Locally staged executable; ignored by Git. |

Docker and Podman have separate volume stores. Changing engines does not migrate authentication or settings. Keep the previous engine/container until a separately reviewed migration is complete. Back up volumes using that engine's normal volume backup tools while the workspace is stopped; protect backups as credentials.

The host proxy is published only on **127.0.0.1:20819**. It is an HTTP CONNECT proxy without authentication; do not expose it to your LAN. Container tools receive `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` (plus lowercase forms) pointing to `http://127.0.0.1:20819`. HTTP-style `ALL_PROXY` works only in clients supporting it. `NO_PROXY` includes localhost. Other containers do not automatically share this proxy.

For native applications, `khroxy apps` shows the dedicated port. Use `khroxy run APP` to set proxy variables for the process, or configure its HTTP/HTTPS proxy explicitly. `khroxy shell-enable` adds optional Bash integration; `khroxy shell-disable` removes only the owned hook. A program can ignore environment proxy settings: native mode is not an egress sandbox.

## Caveman choices and hooks

Choose `--caveman none` (default), `essentials` (Caveman, Megacave, Ultracave), or `full` (all skill directories shipped by the pinned upstream v4.0.1 release). No choice installs or enables third-party SessionStart hooks. New installations have no automatic Caveman mode; invoke installed skills manually. Source: [upstream installation documentation](https://github.com/JuliusBrussee/caveman/blob/v4.0.1/INSTALL.md).

Skills are copied from the image into the persistent agent home, with a checksum manifest. Rebuilding updates unchanged managed copies and refuses to overwrite modified or foreign files. Image contents do not rely on volume copy-up, so existing volumes receive explicit updates too.

```sh
./workspace.sh skills --suite essentials
./workspace.sh skills --suite full
./workspace.sh skills --suite none
```

The image must have been built with `essentials` or `full` to contain the upstream source. Reconfigure/rebuild with your desired choice so it also applies at future container starts. `none` removes only unchanged khroxy-managed skill files. User changes and unrelated skills are retained or reported as conflicts. No command removes authentication.

Older images may have installed hooks and an Ultracave default. Those existing settings are detected and reported, not silently removed or reactivated by installation. Back up and review `~/.codex/hooks.json`, `~/.codex/caveman/`, `~/.agents/skills/`, and `~/.config/caveman/config.json` inside the agent home. Use the matching upstream installer's documented uninstall after reviewing its source if you want its old integration removed; do not delete an entire hook file containing unrelated entries. Installing/trusting hooks is a separate explicit decision outside this installer.

## Security boundaries

The guard checks container identity and a different host network namespace **before** changing any policy. Both IPv4 and IPv6 OUTPUT policies must be DROP. Only the trusted khroxy UID receives upstream network access; agent traffic is restricted to loopback and any explicitly reviewed LAN exceptions. Runtime uses a read-only image, separate Unix users, limited capabilities, and no-new-privileges. Neither engine uses privileged mode or host networking.

These controls are not a complete sandbox proof. The engine owner, host root, trusted khroxy process, image dependencies, kernel, and proxy provider remain trusted. Login volumes are protected by private home/config directories; they are not encrypted. The agent can read its own Codex credentials. Rootless volumes remain accessible to their host owner. Everything in `/workspace`, including secrets accidentally stored in project folders, is accessible and writable to the agent. Never mount a whole home, SSH/cloud credentials, engine sockets, or unreviewed secrets.

Repository ignore rules exclude runtime credentials and generated settings; container build contexts use explicit allowlists. Ignore rules cannot remove past commits or stop deliberate `git add -f`. See [SECURITY.md](SECURITY.md) and [STABILIZATION_REVIEW.md](STABILIZATION_REVIEW.md) for audit evidence and remaining live checks.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `Container startup failed` / `startup readiness timed out` | Read `khroxy-startup: FAILED stage=...` in container logs. Guard success followed by `mkdir: cannot create directory '/home/khroxy': Permission denied` identifies filesystem bootstrap failure, before isolation verification. The corrected image precreates traversable parents; see [startup follow-up](STARTUP_FIX_REVIEW.md). |
| `Container firewall verification failed after startup readiness` | Startup completed, but both DROP policies/guard marker could not be verified. Inspect capabilities, policies, and query diagnostics; access remains refused. |
| `Container did not install IPv4 AND IPv6 OUTPUT DROP policy` | Read `./workspace.sh logs`. `network-guard: FAILED stage=...` identifies namespace, IPv4/IPv6 policy, owner, conntrack, or verification stage. Installer diagnostics distinguish a failed exec/query from a missing DROP rule. Do not bypass the gate. |
| Guard READY, but installer query fails | Check container state and root exec capabilities on Debian. A bootstrap success does not guarantee that a later rootless exec can query netfilter. |
| IPv6, owner-match, or NET_ADMIN failure | Stop for host/kernel/rootless networking review. Never disable IPv6, add privileged mode, use host networking, or edit host firewall rules as a workaround. |
| npm build failure on Podman | Test native proxy; check pasta `-T` support and the chosen build proxy port. |
| Docker build proxy unreachable | Provide a reviewed bridge-reachable proxy; loopback and `host.docker.internal` are different addresses. |
| Workspace offline | Import/select a container profile, press `i`, then verify the proxy. Host profiles are separate. |
| `Fastest pending` | Legacy/changed runtime or unavailable API; finish sessions, perform the planned stop/health/start sequence above. |
| All health tests fail | Check profile expiry, trusted Xray, curl, upstream connectivity, and HTTPS probe availability. No direct fallback is attempted. |
| Existing container blocks setup | Use the upgrade sequence; the installer deliberately does not replace it. |
| Skill conflict | Back up and review the named skill. The installer does not overwrite edited/foreign files. |
| User systemd unavailable | Run from a normal Linux login with a user manager. Container mode does not use systemd. |

The guard's smoke probes check direct public IPv4 HTTPS, TCP DNS, and IPv6 HTTPS, plus both DROP policies. A timeout or unreachable IPv6 route alone does not prove firewall confinement. UDP, alternate DNS, host/LAN access, proxy success, and capability behavior require the live Debian checks in the review document.

## Upgrades and rollback

Back up private native configuration and stopped container volumes before upgrading. Native installation is repeatable and does not restart running proxies. Review the diff, rerun `./install.sh --mode native`, then adopt new runtime features during the planned maintenance stop. The CLI rollback copy is `~/.local/bin/khroxy.previous`; restoring an older CLI does not revert configuration automatically.

The startup filesystem fix requires a rebuilt image and explicit container recreation; updating this checkout or starting an old container does not change its read-only image. The verifier now requires the completed-startup marker from the corrected image. No upgrade is performed automatically.

For a container upgrade, finish sessions, build the new image, then explicitly recreate only the container:

```sh
./workspace.sh stop
# Choose exactly the engine saved in workspace-settings.json:
podman rm khroxy-ai
# Docker users use: docker rm khroxy-ai
./install.sh --mode container --engine podman --projects /srv/work/projects --start
```

Do **not** add `--volumes` or `-v`. Named volumes remain and attach to the replacement. Keep the old image ID if you need rollback, and back up volumes before running a changed image against them. Neither engine nor skill source is upgraded automatically.

## Uninstall

Native preview, then removal retaining profiles:

```sh
khroxy uninstall --dry-run
khroxy uninstall
```

This stops/removes owned proxy, subscription-update, and health units, the CLI, and the owned Bash integration line. For deliberate configuration deletion, use `khroxy uninstall --purge` **instead**, before removing the CLI; it requires `PURGE KHROXY`. Unmanaged files remain. Xray is shared and retained. After reviewing them, remove `~/.local/bin/khroxy.previous` and, only if no other application needs it, `~/.local/bin/xray` manually. Shell PATH changes you made yourself remain yours to remove.

For containers, finish sessions and run `./workspace.sh stop`, then `podman rm khroxy-ai` or `docker rm khroxy-ai`. This retains all projects, credentials, and profiles. Optionally remove the image with your engine's `image rm localhost/khroxy-ai:0.2.0.dev5`. Local `workspace-settings.json` and `docker/xray` may then be removed after review; deleting the checkout removes only installer sources/local staging, not external project directories or named volumes.

To **deliberately erase** saved container authentication/settings, first back them up and verify the chosen engine and volume names. Only then run `podman volume rm khroxy_agent_home khroxy_config` or the Docker equivalent. These optional commands destroy persisted credentials/configuration; no installer or development test runs them. Uninstall does not remove Docker/Podman, Linux packages, unrelated system services, or host SSH/firewall settings.

## Development verification

```sh
python3 -m unittest discover -v
# Optional, localhost fixtures only; uses an existing trusted binary:
KHROXY_TEST_XRAY="$HOME/.local/bin/xray" python3 -m unittest test_xray_routing -v
```

The optional integration test validates the generated Xray configuration and verifies that new Fastest connections switch while an open connection and a fixed assignment remain unchanged. It uses only temporary local fixture processes, not system services or real upstream profiles. Offline tests and local fixtures do not constitute live container isolation acceptance.

Source lineage and normalized versions: [HISTORY.md](HISTORY.md).
