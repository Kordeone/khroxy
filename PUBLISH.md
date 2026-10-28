# Publish the independent standalone release

Publish only after every mandatory gate in `docs/TASKS.md` and `RELEASE_AUDIT.md` passes. Build and verify artifacts from the exact clean committed development revision as described in `docs/RELEASE.md`; retain the successful test log and artifact checksums.

Do not initialize a new repository, rewrite history, merge into main, deploy a container, or include workspace settings/credentials. Publish a tag/release that points to the tested commit, with unchanged verified source/zipapp/checksum assets and accurate limitations. The GitHub release must not claim live-verified optional container isolation, public-network verification, or unsupported XRAT parity.

Validation jobs have read-only permissions. A separate tag-only publishing job has narrowly scoped repository-content write permission and depends on successful real-engine regression/artifact verification. Push the development branch first and require green validation before deliberately creating/pushing `v0.2.0`; the tag job stages a draft and publishes only after asset digest checks pass. If authenticated release publication is unavailable, report the precise blocker and keep the tested local artifacts; never fabricate a release URL or declare an unpublished build released.
