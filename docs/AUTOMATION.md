# Automation, exports and optional local API

The original CLI and TUI retain their output and behavior. Additive `automation` commands emit schema-1 JSON on stdout. Successful commands return 0; rejected operations return 1 with an error on stderr, argument errors return 2. Existing `test-profiles`, `results`, storage, engine and scanner commands also provide structured results. Use `--help` for bounds and options.

```
khroxy automation status
khroxy automation profiles --limit 100 --offset 0 --protocol vless --enabled true
khroxy automation profiles --search project
khroxy automation select my-profile --target active
khroxy automation select my-profile --target fastest
khroxy automation rotate
```

Select/rotate use existing measurements; they never initiate network tests. Fastest requires a fresh successful health measurement and respects eligibility/pins. Manual rotation is deliberate and can override a schedule's waiting period. An empty healthy set fails without direct fallback. Live active-route edits are rejected to preserve sessions. Live Fastest changes use native session-safe control; unavailable control produces an explicit pending result instead of restarting the core. Configuration locks have a bounded wait for automation requests. Fixed application mappings remain fixed.

Exports are explicit, private and validated by reimport before writing:

```
khroxy export-data
khroxy export-data --format json --profile my-profile --include-secrets --output private-subscription.json
khroxy export-data --format base64 --include-secrets --output private-subscription.b64
khroxy export-data --format sip008 --profile shadowsocks-profile --include-secrets --output private-sip008.json
khroxy export-data --format backup --include-secrets --output private-backup.json
```

Default output contains names/protocols only. Credentials are never printed; credential exports require both flags, create new mode-0600 files, and refuse overwrite/symlinks. JSON is KHROXY's supported canonical upstream envelope (`outbounds`), importable through `import-data`; base64 encodes the same envelope. It is not a runnable Xray configuration for protocols Xray does not support. SIP008 accepts raw Shadowsocks profiles only; unsupported settings fail rather than disappear. Subscription exports contain at most 100 profiles and 1 MiB; filter larger collections explicitly. Full backups preserve settings/history/ownership and use the storage backup limits. Never publish private artifacts.

The API is an optional foreground process. It never installs or starts a service automatically:

```
khroxy api token --output "$HOME/.config/khroxy/api.token"
khroxy api serve --token-file "$HOME/.config/khroxy/api.token" --port 21090
```

Only `127.0.0.1` and `::1` bindings are supported. All supported reads and mutations require `Authorization: Bearer TOKEN`. Keep the token in a private file owned by the current user; do not put it in shell history, logs, URLs or command arguments. Use a client that reads that file and supplies the header. No cookie authentication or CORS is provided; browser Origin requests and unexpected Host headers are rejected. External binding is deliberately unsupported: KHROXY does not provide an unsafe plaintext public management service.

| Endpoint | Method | Body/query |
| --- | --- | --- |
| `/v1/status` | GET | None; sanitized runtime/selection summary |
| `/v1/profiles` | GET | `limit` 1..1000, nonnegative `offset`, optional `protocol`, `enabled=true/false`, `search` |
| `/v1/selection` | POST | JSON `{"name":"my-profile","target":"active"}` or `fastest` |
| `/v1/rotation` | POST | JSON `{}`; rotate using existing fresh health |

No endpoint returns credentials, raw configuration, subscription URLs, private exports, arbitrary filesystem data or arbitrary commands. Request headers are bounded to 16 KiB, JSON bodies to 4 KiB, concurrent handlers to eight, socket reads to three seconds. API errors are generic JSON, intentionally withholding private upstream details. Stop with Ctrl+C/SIGTERM; the independently supervised proxy continues until deliberately stopped. Core startup recovery belongs to the supervisor, not this API process.

Print shell completion source with `khroxy completion bash`, `zsh`, or `fish`, then source/install it using your shell's normal procedure. Nothing is injected into shell configuration automatically. `khroxy manual` prints a roff manual; `khroxy manual > khroxy.1` then `man ./khroxy.1` views it. Command-specific argument completion is not provided.

Desktop integration was evaluated: COSMIC, NetworkManager and system proxy settings affect unrelated applications and differ by desktop. This release deliberately offers per-application launchers/environment output and explicit loopback listeners instead. It does not change desktop or host configuration.
