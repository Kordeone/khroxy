# Local storage and migration

New native installs and fresh `khroxy init` create a local SQLite database. Existing JSON-only installations keep working until `khroxy storage migrate` or a native installer upgrade. No PostgreSQL server, controller, network connection, or third-party Python driver is required.

Migration validates schema version 1, retains the exact original `state.json`, writes a private `state.json.pre-sqlite` copy, builds/integrity-checks a temporary database, then atomically installs `state.sqlite`. A migration lock prevents duplicates; repeating succeeds without replacing existing data. An interrupted migration leaves the source/backup recoverable. After migration, SQLite is authoritative: the preserved JSON is a rollback source, not a live projection. Do not edit the old JSON to change current state.

Database transactions preserve all profile, application, subscription, and unknown configuration fields. Profiles and subscriptions have normalized records. Profiles can be disabled, soft-deleted, restored, or deliberately purged; history, metadata, rotation and session records have separate tables. Referenced profiles cannot be deleted. Profile eligibility enforcement belongs to the health/rotation work tracked in T07; do not interpret a stored disable flag alone as termination of an existing session.

```sh
khroxy storage status
khroxy storage migrate
khroxy storage backup /private/path/khroxy-backup.json
khroxy storage history --profile main --limit 50
khroxy profile disable spare
khroxy profile enable spare
khroxy profile delete spare
khroxy profile restore spare
khroxy profile delete spare
khroxy profile purge spare --confirm PURGE
```

Backups contain credentials. They are mode 0600, never overwrite existing paths, and must not be committed or uploaded. Configuration directories are mode 0700; databases and migration copies are 0600. SQLite transactional journals stay in the same private directory. This provides access control, not encryption against the owner or host root.

To restore, finish sessions and stop the managed proxy first, then run `khroxy storage restore /private/path/khroxy-backup.json`. The command refuses to interrupt a running proxy. It validates the bounded JSON backup before any database change, creates a private pre-restore snapshot under the same write transaction, and replaces records transactionally. Restore does not start a service. Pre-restore snapshots are retained for recovery and are not silently removed by uninstall.

Rollback to the previous JSON implementation requires stopping all KHROXY writers, taking a fresh backup, and restoring a matching older executable/configuration snapshot. The original `state.json.pre-sqlite` can restore pre-migration state. It does not contain changes made after migration. Do not delete the database or overwrite the original JSON until a backup has been verified. A current-to-legacy export/migration rollback workflow will be validated at release packaging (T12).

## PostgreSQL evaluation

PostgreSQL would require a separately managed server, authentication, an extra driver and cross-backend transaction conformance. None is needed for the local CLI/TUI or concurrent local workload. This task introduces explicit storage boundaries for a future adapter but does not add a runtime server dependency or claim PostgreSQL support. SQLite remains the v0.2.0.dev10 local-first backend; optional PostgreSQL parity is evaluated as unsuitable for the mandatory local runtime, not silently emulated.
