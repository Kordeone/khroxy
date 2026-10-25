# Changelog

## 0.9 — Rootless Podman conversion (October 2026)

- Switched the workspace installer from Docker Compose to **Podman directly** (`podman build/run/exec`). No Docker or Compose required.
- Kept the exact four-screen khroxy TUI and its keybindings.
- Kept the setup-time prompt for the host projects folder, mounted read/write at `/workspace` using rootless Podman `--userns=keep-id`.
- Preserved one combined container with Node 24, npm/npx, Codex, Caveman/Ultracave, Xray, and separate persistent Codex and khroxy volumes.
- Restricted proxy publication to host `127.0.0.1:20819`; rootless `pasta` for runtime network, no host network mode.
- Replaced Docker-specific `eth0@if` guard with Podman marker plus a host-vs-container network namespace check before firewall changes.
- Added rootless preflight, no automatic Podman install, explicit start authorization, and container isolation smoke checks.
- Offline verified; **live Podman networking and kill-switch checks remain outstanding.**

See older v0.8 packages for prior release history.
