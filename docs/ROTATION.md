# Health monitoring and automatic rotation

The optional health watcher still runs independently of the TUI. Its default Fastest policy uses latency hysteresis. Scheduled/failure-driven rotation is an additional opt-in policy scoped to `@fastest` assignments; it never rewrites fixed mappings or the manually chosen `@active` profile.

```sh
khroxy rotation status
khroxy rotation enable --interval 1800 --cooldown 300 --failure-threshold 2
khroxy rotation now
khroxy rotation disable
khroxy rotation pin main
khroxy rotation pin main --off
```

A per-profile pin keeps that profile selected once it is the current Fastest choice, even if a faster candidate appears. Pinning a candidate does not immediately move existing routes to it. If the pinned endpoint fails, status reports it unavailable rather than silently unpinning/falling back. A disabled profile is ineligible for automatic selection even if previously pinned. Existing explicitly fixed application sessions are not stopped merely because their profile is disabled.

Enabled rotation confirms consecutive failures before replacing the current profile. Failed candidates receive a cooldown, and successful probes clear their failure counters. Healthy choices are not needlessly replaced during the global cooldown. When the configured interval is due, an alternate fresh healthy profile can be selected intentionally, without requiring a latency improvement. `now` bypasses scheduling, not security checks or disabled-profile eligibility. If no candidate is healthy, existing assignments remain explicitly unavailable and no direct route is created.

Manual active selection has precedence for `@active`; fixed mappings remain fixed. `@fastest` deliberately remains an independent automation assignment. Disabling scheduled rotation retains the current proxy and ordinary Fastest health checks. The health service itself can be stopped independently if checks are unwanted.

## Native live switching and recovery

Xray and V2Ray use native RoutingService over a filesystem Unix socket in the mode-0700 configuration directory. V2Ray's domain-socket API transport does not open its nominal API port on TCP. sing-box uses its built-in selector API, bound strictly to 127.0.0.1 with a random persistent secret. No external controller, browser UI, XRAT or Mihomo process is involved. This endpoint is generated only for a runtime needing session-safe Fastest; it is distinct from KHROXY's optional management API.

Native selectors retain all candidate outbounds and leave existing streams attached. Failed/ambiguous switch replies trigger an idempotent rollback to the prior target. If target restoration cannot be verified, automation is suspended and reports the uncertainty. Inspect `rotation status`; stop the owned runtime for maintenance before re-enabling rotation. New/edited profile definitions or old runtime configurations require a planned stop/health/start to install the updated candidate pool. No automatic restart is used as a shortcut.

Prepared runtime files are compared with both stored selection and installed definitions so a crash between file preparation and API application cannot make the next health cycle skip the RPC. Applied rotation history is stored only after the runtime/configuration operation succeeds. The runtime supervisor recovery work is tracked separately in T08.

Validation uses all three real engines with labelled local upstream streams. New streams switch, original streams remain on their original target, fixed mappings stay fixed, unauthenticated selector control fails, and a deliberately lost successful reply is rolled back through the real native API. These tests do not claim live Internet outage or container isolation acceptance.
