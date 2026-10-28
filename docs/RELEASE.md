# Standalone release, acceptance and rollback

Publication requires successful regression, packaging and installation validation. No incomplete feature becomes out of scope merely by documenting it. The native application is the v0.2.0 release target. Historical Windscribe controllers are excluded from artifacts; optional AI-container live acceptance is explicitly not claimed.

## Reproduce validation

Use Linux amd64, Python 3.10+, curl, openssl, and the optional trusted engines pinned in `ENGINE_LOCK.json`. `python3 -m tests_support.fetch_engines --output /tmp/khroxy-test-engines` downloads only official release archives, checks pinned SHA256 before extraction/execution, and writes no host binary/service. An archive mismatch aborts. This test helper needs network access; runtime KHROXY never requires the helper or all three engines.

```sh
KHROXY_TEST_XRAY=/tmp/khroxy-test-engines/xray \
KHROXY_TEST_V2RAY=/tmp/khroxy-test-engines/v2ray \
KHROXY_TEST_SING_BOX=/tmp/khroxy-test-engines/sing-box \
python3 -m unittest discover -v
```

Tests use temporary HOME directories, fake credentials and local endpoints. Actual engines/processes handle protocol, TLS/QUIC, routing, DNS, session switching, daemon recovery and API behavior. A test-only user-bus shim verifies uninstall file behavior without invoking host services; it is not live systemd acceptance. Static generated units are verified with the real `systemd-analyze --user verify` when available.

For additional external-network isolation, run networking tests inside a disposable rootless user/network namespace:

```sh
KHROXY_TEST_XRAY=/tmp/khroxy-test-engines/xray \
KHROXY_TEST_V2RAY=/tmp/khroxy-test-engines/v2ray \
KHROXY_TEST_SING_BOX=/tmp/khroxy-test-engines/sing-box \
unshare --user --map-root-user --net python3 -m tests_support.netns test_networking
```

The helper brings up only loopback **inside the new namespace**. It never changes a host interface/firewall or disables IPv6. A nested mapped-user CLI fixture preserves the application's non-root restriction. Hosts that disallow user namespaces must report that limitation rather than weaken isolation. The normal real-engine suite uses explicit loopback fixtures and no public test endpoint.

Compile Python, check shell syntax, run ShellCheck when available, render the roff manual and verify the preserved UI AST contract. CI performs syntax, shell, real-engine regression and deterministic packaging/install checks with read-only validation permissions and pinned official actions. A separate write-scoped job runs only for the authorized repository’s `v0.2.0` tag after validation succeeds: it re-verifies retained artifacts, stages a draft, checks uploaded asset digests and only then publishes. It never deploys services. Development-branch CI cannot publish.

## Reproducible artifacts

```sh
python3 build_release.py --output /tmp/khroxy-release-a
python3 build_release.py --output /tmp/khroxy-release-b
diff -r /tmp/khroxy-release-a /tmp/khroxy-release-b
python3 verify_release.py /tmp/khroxy-release-a --xray /tmp/khroxy-test-engines/xray
```

Build from a clean committed tree. `--allow-dirty` exists only for development tests; its manifest is marked and cannot pass production artifact verification. `SOURCE.json` identifies the exact Git commit, version, epoch and SHA256 of every source input. `SOURCE_DATE_EPOCH` can explicitly override the timestamp; otherwise the commit timestamp is used. Tar ownership/modes/order, gzip timestamp and zip metadata/order are deterministic. Reproducing exact compression bytes across different Python/zlib releases is not promised; record the build toolchain.

Artifacts: `khroxy-VERSION-source.tar.gz`, `khroxy-VERSION.pyz`, `SHA256SUMS`. The executable zipapp contains only the independent CLI/modules and source manifest; it needs Python and a separately installed supported engine. The source archive contains installer/docs/tests and the optional original AI-workspace tooling, without engine binaries, credentials, workspace settings, authentication, databases, project mounts, cached packages or Windscribe experiments. Engines retain their upstream licenses and are not bundled. No new Python/npm runtime dependency is introduced.

The verifier checks archive paths/types/bounds, checksums, exact source/zipapp implementation identity, version/startup and a real native installation in a temporary HOME. It starts no user service. Keep the tested artifact bytes unchanged when uploading; checksums must match the tested commit. A source archive can rebuild itself using its validated manifest. An executable zipapp is useful for foreground CLI/daemon operation; use the source installer for managed user units and module installation.

## Install and upgrade

Verify `sha256sum -c SHA256SUMS`, extract the source archive into a fresh directory, and run `./install.sh`. Choose a trusted matching-architecture optional engine. No sudo or host package installation is required. Existing JSON state migrates atomically with a private original backup; SQLite then becomes authoritative.

Before upgrading:

1. Export a private full SQLite backup; preserve the matching old source/artifacts.
2. Stop the optional API and owned supervisor deliberately. Stop only owned proxy, health and subscription services if enabled. Do not stop SSH, proxy, VPN or other unrelated services.
3. Run the new installer's dry run, then normal native installation. It refuses foreign paths and live runtimes; no session is killed silently.
4. Review native `engine check`/status and deliberately enable services when ready.

Immutable CLI/module/owned-unit snapshots are created under `~/.local/lib/khroxy/rollback-code/HASH`. Existing files and state are restored after injected installation-write failure; snapshots/backups are retained for review. Individual filesystem writes are atomic; power loss across several writes is not a filesystem-wide transaction. Keep the matching source and snapshot for recovery before restarting writers. Database operations remain transactional.

## Rollback without destroying data

A code rollback is explicit:

```
./install.sh --rollback-code SNAPSHOT_ID
```

It validates snapshot member hashes/path/ownership, refuses live runtimes/foreign destinations, snapshots the displaced code, restores CLI/modules/owned units and leaves all state untouched. No service is started. Inspect the snapshot's `manifest.json` and retain your data backup. Restoring an earlier SQLite-capable implementation requires compatible schema/feature support; do not infer compatibility merely from a successful code copy.

For a pre-SQLite downgrade, the preserved legacy JSON is an older point-in-time state. A tested fixture uses the original standalone program from commit `bab0e22`; CLI `profiles`/`apps` read the exact migrated JSON successfully after code rollback. This proves original-format recovery, not automatic conversion of later v0.2.0 features.

If later v0.2.0 data exists, stop every writer and keep both a fresh private SQLite export and the database. Restore matching old code plus the original JSON into a **separate private configuration backup/home first**, verify its profiles/apps, then make a deliberate choice about the older snapshot. Do not delete SQLite, overwrite newer JSON, remove volumes or silently discard new features to force a downgrade. On returning to v0.2.0, changed legacy state versus its migration backup is detected; reconcile it explicitly rather than choosing a winner automatically.

## Uninstall and retained files

`khroxy uninstall --dry-run` previews owned scope. Normal uninstall stops only identity-verified owned API/core processes and owned user services; removes installed managed CLI/modules/bytecode/units/hooks; keeps SQLite/profiles/authentication/exports/code snapshots and independently installed engines. Foreign files and unsafe symlinks are retained/refused.

`--purge` additionally requires the exact confirmation and removes known configuration/runtime/generated backup files. Unknown files, external exports/tokens, immutable code snapshots and all projects/volumes are retained. Inspect retained code snapshots under `~/.local/lib/khroxy/rollback-code/` and delete them manually only after deciding no rollback is needed. Container removal never implies persistent-volume removal. No volume prune is part of installation, upgrade, rollback or uninstall.

## Required live Debian follow-up

Real host boot/user-service enablement, arm64 packaging and optional container netfilter/UID-map/pasta/Docker/Codex authentication behavior are not inferred from local fixtures. The native release does not require the optional container. Before treating that workspace as an approved isolation boundary, follow `SECURITY.md` on the actual authorized Debian test host without changing host firewall/SSH or production containers. A startup marker/guard log is not independent firewall verification.
