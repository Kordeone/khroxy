# Optional rootless runtime supervisor

The original systemd-backed proxy and four-screen TUI remain available. The optional supervisor owns proxy-engine processes independently of the TUI, with a private Unix socket and persistent session identity. It does not modify host networking or install a controller.

Finish sessions and stop any legacy managed proxy before changing runtime backends:

```sh
khroxy daemon start
khroxy daemon proxy-start
khroxy daemon status
khroxy tui
khroxy daemon proxy-stop
khroxy daemon stop
```

`daemon start` starts only the management process, without starting a new proxy unless recovering an explicitly desired owned session. `proxy-start` or existing `khroxy install` starts the configured proxy. Repeated start/stop operations are idempotent. Closing the TUI does not stop an intentionally backgrounded runtime. `daemon stop --keep-runtime` intentionally retains the owned proxy for later adoption; `daemon disable` stops it and returns subsequent legacy commands to the original backend. No foreign listener or process is adopted merely because it uses the expected port/name.

When supervisor mode is enabled, existing proxy start/stop/restart/status operations delegate to its private control path; other user-service commands remain separate. New native proxy units include a valid negated mode condition to avoid starting a second legacy engine when supervisor mode is active. For an older unit lacking that condition, explicitly disable its autostart before installing the optional supervisor unit.

## Ownership and recovery

Managed identities include Linux boot ID, PID/start ticks, effective executable inode/device, exact argument vector, UID and a random per-launch environment nonce. Destructive signals use pidfds and recheck ownership; PID reuse, wrong boot IDs, missing nonces, foreign processes and legacy integer-only PID records never authorize signalling. This requires Linux pidfd support; missing support fails safely rather than falling back to an unsafe raw PID kill.

The private directory is 0700, IPC socket/records/configuration snapshots are 0600, and socket peers must match the server's kernel-reported UID. Requests have size/time/concurrency bounds and a fixed operation set, not arbitrary command execution. Host root and the same account remain trusted, as with normal user-level applications.

An immutable private configuration and launch intent are written before spawning an engine. A crash between spawn and PID-record commit can recover only the exact nonce/command-matched process. A supervisor SIGKILL leaves an intentionally backgrounded owned engine running; a restarted supervisor adopts it. Stale/dead sessions never authorize killing a reused PID. Valid desired configuration is rebuilt for recovery, and startup failures are bounded to three attempts within 60 seconds before explicit offline/blocked status. A manual start resets the retry budget. No direct route is substituted.

SIGTERM performs bounded clean termination of owned engines. Reboot simulation invalidates old boot identities and restores only valid desired configuration. Active stream loss after an actual engine crash is unavoidable; supervisor recovery does not claim otherwise.

Legacy container PID tracking is also hardened: new records use the same ownership proof and pidfd signalling. An old integer-only `xray.pid` is not enough to adopt/stop a matching process. Recreating a container safely resolves old unowned runtime processes; do not signal arbitrary recorded PIDs manually.

## Optional user unit

```sh
khroxy daemon service          # Preview only
khroxy daemon service --write  # Prepare an owned user unit; never start it
# Enable only when you choose to use this backend:
systemctl --user daemon-reload
systemctl --user enable --now khroxy-supervisor.service
```

Only an explicit `--write` prepares the user unit. A foreign/symlink unit is refused. This development work never installed or started a real host unit. Static verification of generated units and isolated daemon lifecycle/reboot tests are separate from live host reboot acceptance.

Uninstall stops verified owned supervised processes and removes owned units/CLI while retaining profiles and private storage by default. Saved session snapshots and backup files may contain credentials; keep them private. Deliberate purge/rollback procedures are covered by release packaging acceptance.

Shutdown completion: `daemon stop` waits for both the owned daemon and engine to exit before reporting success; callers may then safely remove temporary runtime records. `daemon stop --keep-runtime` explicitly waits for daemon exit while preserving the verified engine for adoption. Shutdown prevents queued recovery/manual starts, and recovery rechecks `desired` under the runtime lock so a stale health iteration cannot revive a stopped engine. Native tests cover immediate record removal, keep/adoption and stale recovery; the full-suite process audit reports zero leftover generated engines.
