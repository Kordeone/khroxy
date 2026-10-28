# Source provenance

This branch is a present-day reconstruction of existing KHROXY code.
Original commit dates and the original repository are preserved separately.
The available original history starts at commit `8ad70c7`, labeled v0.7.1.
No authentic v0.1.x source commit or tag was found.
The v0.1.0 identifier is a normalized baseline label, not a recovered release.
Development versions identify snapshots, not patch-release tags.
No new tags have been created.

## Included source milestones

- `c7dc4d4841b1838f3465e793fdfa6016ed6612fa`: Import normalized standalone baseline with original TUI and stabilized installer.
- `4bc8b99256836c0ed6118f8fd9d59b7b29b95e99`: Add transactional SQLite storage and recoverable legacy migration.

The initial snapshot includes earlier standalone stabilization work, with
Windscribe experiments and internal review reports excluded. The portable
TUI contract test is carried forward from the final original snapshot.
The legacy compatibility fixture has a privacy-sanitized NO_PROXY default; TUI function ASTs remain unchanged.
