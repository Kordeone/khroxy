# Changelog

## v0.9.1 — rootless Podman build-proxy repair

- Fixed container image npm download timeout by routing build-time package downloads through an existing native khroxy HTTP proxy (default `127.0.0.1:20820`).
- Rootless `pasta:-T,PORT` forwards only the build proxy port; **no `--network=host`**.
- Probes the npm registry through the host proxy and tests Podman-to-host proxy TCP connectivity before a costly image build. Both checks fail early and never fall back to direct access.
- Build proxy settings are scoped to the `podman build` process; runtime `podman run` and the AI isolation policy are unchanged.
- Supports choosing a different build proxy via `python3 setup.py --build-proxy-port PORT`.
- Added regression tests for proxy routing, failure handling, network boundaries, and port validation.

## v0.9 — Podman container

Rootless Podman replacement for the Docker Compose workspace, preserving the four-screen khroxy TUI, Codex, Ultracave, separate auth volume, projects mount, and fail-closed runtime guard. Live network isolation remains unverified.
