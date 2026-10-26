# Native Khroxy separation contract

The `khroxy` repository owns the standalone Linux Xray profile manager:
CLI/TUI, profiles, subscriptions, per-application proxy setup, logging,
user-scoped service integration, and its own interactive `install.sh`.

## Current scope

The existing `khroxy` Python entrypoint remains unchanged. `install.sh`
currently performs **read-only inspection**; it intentionally does not install
or modify existing user services. The old rootless Podman workspace code is
retained temporarily for compatibility, pending migration and independent
acceptance tests.

The separate [khagent-khontainer](https://github.com/Kordeone/khagent-khontainer)
repository will own Podman, isolated networking, optional Windscribe backend,
AI agent selection and installed agent versions. It depends on a **pinned,
verified release** of this native component, never the other way around.

## Split acceptance gates

1. Preserve native CLI/TUI functionality and user configuration.
2. Introduce explicit versioned installation artifact and independent installer.
3. Import the version-pinned native artifact in Khagent without copying source.
4. Prove full-container tunneling (TCP, UDP, DNS, IPv4/IPv6) and fail-closed
   behavior in isolated Podman before installing AI agents.
5. Confirm optional Windscribe can run inside the selected rootless container
   without modifying host Windscribe, routing, firewall, SSH or hotspot.
6. Migrate obsolete container files only after feature parity and safe rollback.

Do not treat the legacy container HTTP proxy as a verified whole-container
tunnel. All live Debian deployment requires separate approval.
