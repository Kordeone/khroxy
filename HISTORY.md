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
- `0bc434b22e927580c93ffbf8bb6558e6f996b6d5`: Expand bounded imports with deduplication and shared source ownership.
- `5e35a16e66e8259c7646bf6b393498f477255ea8`: Support optional Xray, V2Ray and sing-box engines.
- `bed300e2f25d7b0db8a2c9e384847e2d4ded2049`: Add bounded concurrent proxy probes and owned-process cancellation.
- `1aeed9c79f242df87083c3d9141c8fdf33056ac3`: Persist diagnostics and rank fresh measurements with offline metadata.
- `1227e85ad3b98e3116e9a6ae42074e340f4694c3`: Add bounded rotation and session-safe native selectors.
- `5da23f2f88ab54d99d164709e41d4ef48700855b`: Add rootless runtime supervisor with private IPC and recovery.
- `1b643a9b871d61ff52b350bd45de6acecd33542d`: Add loopback listeners, explicit routing and native static DNS.
- `8b7e32cd66ecc169e63c0c7bb66c8830988a0068`: Add bounded resumable explicit-candidate TCP scanning.
- `6e4fad6fb9f01d02ba075390eff8767aa817ca63`: Add authenticated local API, automation, exports and shell documentation.
- `1d702cea5af19680b82bb895959e3a6fe216aea8`: Prepare v0.2.0 packaging, safe upgrades and gated publication.

The initial snapshot includes earlier standalone stabilization work, with
Windscribe experiments and internal review reports excluded. The portable
TUI contract test is carried forward from the final original snapshot.
The legacy compatibility fixture has a privacy-sanitized NO_PROXY default; TUI function ASTs remain unchanged.

## Privacy and release preparation

The final commit adds explicit public-document packaging boundaries and privacy
regressions reviewed independently. Personal infrastructure wording and stale
image examples were corrected in every reconstruction snapshot. Runtime and
test source bytes for those documentation-only revisions match the snapshots
that passed the recorded regression suites. Source commit IDs identify input
lineage; the old publication records are not claims about this candidate.
