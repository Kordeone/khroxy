# khroxy v0.9 — rootless Podman AI workspace

This release swaps khroxy's **Docker Compose** workspace preview for **rootless Podman**. It requires **no Docker daemon, Docker CLI, Docker Compose, or rootful Podman**.

The native khroxy Python TUI and CLI are unchanged: four full-screen views (Profiles, Applications, Subscriptions, Logs), `?` for scrollable keybindings, `*` for the active profile, testing and fastest profile selection. Your native profiles and subscriptions are not moved, deleted, or copied to the container.

**Security status:** Offline installer and script tests pass, but the Podman image and isolation policy have **not** been run on your Debian host. This is a staged deployment with a fail-closed startup check, **not a verified VPN kill switch**.

## What it installs (inside ONE container)

- Node.js 24 with npm/npx; Codex CLI version 0.164.0; Caveman integration with Ultracave default (Caveman/Megacave/Ultracave modes).
- Locally staged, user-approved Xray binary and khroxy gateway/TUI.
- A separate persistent Codex home and login (named volume `khroxy_agent_home`). Not copied from Debian's `~/.codex`.
- A separate persistent khroxy configuration volume (`khroxy_config`).
- Exactly one selected, existing Debian project folder mounted **read/write** at `/workspace`; you choose the host folder during setup.
- A local HTTP proxy published on **127.0.0.1:20819** on Debian. No LAN ports published.
- No trusted LAN exceptions initially. No direct public network from the unprivileged agent under the installed namespace-local OUTPUT DROP policy.

**Note on container networking:** The Podman preview uses private rootless **pasta** networking. The published proxy is accessible from the **Debian host only** through `127.0.0.1:20819`. It is **not automatically accessible to other Podman containers**; that requires a separately configured trusted shared network or relay and its own access tests. Don't use `--network=host` as a shortcut.

## On Debian 13 (run as `rapper`, NOT sudo)

Check Podman is installed:

```sh
podman --version
podman info --format json
```

If missing, Debian's normal package command is:

```sh
sudo apt install podman passt uidmap
```

This is a **manual** host package installation. The setup script never runs `sudo`, changes AppArmor/firewall/SSH, installs packages, or unmasks services. Rootless Podman needs working subordinate UID/GID mapping. The installer validates Podman is rootless before building.

Unpack ZIP and enter the `khroxy-v0.9` folder. Preview changes with a project path (any existing owned projects directory):

```sh
python3 setup.py --projects /mnt/storage/Projects --dry-run
```

Run the interactive installer to **choose the actual path during installation**:

```sh
python3 setup.py
```

The installer shows the path and available Xray binary, stages its own copy, and *asks* if you want to build and later start. It refuses to replace a container with the same name. `--configure-only` is safe when Podman is not installed; `--build` builds but doesn't start. `--start` still requires typing `YES`.

Start requires a valid Xray binary, Podman rootless preflight, separate Podman network namespace, and a container firewall policy that defaults to **DROP** for IPv4 and IPv6. A failed guard or failed bypass smoke test stops the container without deleting the persistent volumes.

## Workspace commands (after explicit approval and successful start)

```sh
./workspace.sh status                # podman status
./workspace.sh tui                   # existing four-screen khroxy TUI
./workspace.sh login                 # authenticate Codex in its own persistent home
./workspace.sh codex -c 'shell_environment_policy.inherit="all"'
./workspace.sh shell                 # /workspace as unprivileged agent
./workspace.sh verify                # direct egress smoke test
./workspace.sh logs                  # startup logs
./workspace.sh stop                  # leave volumes and login untouched
./workspace.sh start                 # restart only an already created container
```

Inside the TUI, import/select a profile and start the gateway. The host proxy at `127.0.0.1:20819` is **not usable without a working Xray profile**; the agent should remain blocked from direct public network when it is down.

## Practical security boundaries

- Runs **rootless**; container startup uses Podman's `--userns=keep-id`, `--network=pasta`, `--cap-drop=all`, scoped capabilities for isolated firewall setup, read-only root filesystem, restricted tmpfs, and a localhost-only published port.
- Container root is still privileged *inside its user/network namespace*. The AI commands use unprivileged `agent` UID; proxy runs as a separate unprivileged `khroxy` UID.
- Before touching firewall rules, the guard checks for a Podman container and refuses if the namespace inode matches the host namespace recorded by setup.
- OUTPUT default is DROP for IPv4 and IPv6. Xray/khroxy UID may reach configured remote servers, while agent UID is limited to loopback and future explicit trusted LAN exceptions.
- Fail-closed means no permission to bypass the proxy through ordinary direct sockets. It doesn't defend against kernel exploits, malicious root in container, or poorly tested Podman networking. Direct TCP checks **do not prove** DNS/UDP/IPv6 leak resistance or true transparent proxying.
- Only the setup script's private network configuration has been coded and unit tested; **live Podman, iptables and DNS/UDP tests are still required** before using this for untrusted agents.
- The container's AI tools use proxy environment variables. Applications that ignore proxy settings are blocked from public Internet, **not transparently forwarded**.
- No Docker socket or host Podman socket is mounted; no Debian home mount, no trust-LAN allowlist by default; the only host data mount is the selected projects directory.
- The build downloads base image/npm/Caveman sources using build-time networking. Build happens *outside* the protected runtime proxy and must be reviewed separately; dependencies are pinned to declared versions/refs but not cryptographic digests.

## Troubleshooting

- **Missing Podman:** The script prints a manual Debian install command; it will not install Podman automatically.
- **Rootful Podman:** Rejected. Run as your normal Debian user without `sudo`.
- **Unable to set iptables/ip6tables inside rootless Podman:** Startup fails; do **not** bypass the guard, add `--privileged`, or use host networking. Check kernel/netfilter availability and Podman networking before any further changes.
- **Port 20819 in use:** Stop whichever service occupies the port or choose a reviewed port change; never publish globally as a quick fix.
- **Codex login fails:** Import/select a usable khroxy profile first, then login; ensure no stale 127.0.0.1:20809 proxy settings inside Codex's persistent environment.
- **Can't write projects:** Verify the chosen folder is writable by the host user. The installer uses `keep-id` to map that user; it never recursively `chown`s the host folder.

## Audit

All offline tests may be run without Podman:

```sh
python3 -m unittest discover -q
sh -n workspace.sh docker/network-guard.sh docker/entrypoint.sh docker/scheduler.sh
```

GitHub publication includes no subscription credentials, state files, Xray executable, Codex login, or mounted project contents. See `SECURITY.md`, `RELEASE_AUDIT.md` and `.containerignore`.
