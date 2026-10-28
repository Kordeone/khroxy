# Optional proxy engines

KHROXY independently generates configurations for optional Xray-core, compatible V2Ray-core v5, and sing-box >=1.13. Engines are separate trusted executables; none is a controller dependency. Xray remains the legacy default. The application never downloads/executes XRAT, Windscribe or Mihomo.

```sh
khroxy engine list
khroxy engine check
khroxy engine preview
khroxy engine preview --output /private/native-config.json --include-secrets
# Finish sessions and stop your managed proxy before selecting a different core:
khroxy engine select sing-box --binary /absolute/path/to/sing-box
khroxy engine check
khroxy install
```

Selection validates the native configuration before storing state. It does not start a process, interrupt sessions or silently rewrite security options. `khroxy install` prepares the selected core's user service. `engine select auto` tries installed engines against the current routes and stored profile options. Missing/incompatible cores leave state/runtime unchanged. Manually selecting a core validates the current runtime; unassigned profiles may still require another compatible core when used.

Existing JSON outbounds stay in the canonical format, so switching engines does not mutate credentials or source ownership. Supported combinations include VLESS, VMess, Trojan, Shadowsocks, HTTP/HTTPS and SOCKS5 with the three engines; Hysteria2 uses sing-box. Xray-only XHTTP/ECH options, REALITY post-quantum fields, or unsupported gRPC/mux/transport options are rejected when another adapter cannot represent them. No option is silently stripped to make a configuration pass.

Session-safe Fastest now uses private native Unix routing APIs for Xray/V2Ray and an authenticated loopback selector API for sing-box. Existing streams and fixed assignments remain unchanged. Legacy or changed candidate pools still need planned maintenance; see [rotation policy and recovery](ROTATION.md). Closing the TUI never owns a foreground proxy's lifecycle.

A native install can use sing-box without installing Xray:

```sh
./install.sh --mode native --proxy-engine sing-box --engine-binary /trusted/path/sing-box
```

`--xray` remains supported. The installer does not install packages, download engine binaries, or change host networking. Obtain trusted binaries independently; ELF identity, version and native validation checks are compatibility checks, not authenticity verification. Configuration previews redact entire settings/transport subtrees by default; secret-bearing native configuration can only be written to an explicit new private file.

## Real-engine acceptance

Test-only binaries were fetched from official release assets into `/tmp`, never installed into the host or bundled into KHROXY:

| Engine | Tested version | Official archive SHA-256 |
| --- | --- | --- |
| Xray | 26.9.9 | Existing trusted installed binary; no download performed |
| sing-box | 1.14.4 | `699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968` |
| V2Ray | 5.53.0 | `6bbb8aee65a57d0b12599b4b7c842b3ad0daca4436e661d94015c447cb31b4fa` |

The test starts a disposable sing-box upstream/origin on loopback and real clients for all listed supported protocols, including HTTPS and Hysteria2. A generated local CA is explicitly trusted by test child processes; verification is never disabled. Server routing permits only loopback and rejects everything else. Separate CLI tests validate engine switching in both directions and a sing-box-only native installation with an isolated home and nonexistent test session bus.

Sources: [sing-box protocol configuration](https://sing-box.sagernet.org/configuration/outbound/), [sing-box routing actions](https://sing-box.sagernet.org/configuration/route/rule_action/), [V2Ray commands](https://www.v2fly.org/en_US/guide/command.html), [Xray HTTP upstreams](https://xtls.github.io/en/config/outbounds/http.html). Optional engine licenses remain upstream: [Xray MPL-2.0](https://github.com/XTLS/Xray-core/blob/main/LICENSE), [V2Ray MIT](https://github.com/v2fly/v2ray-core/blob/v5.53.0/LICENSE), [sing-box GPL-3.0-or-later with its additional naming notice](https://github.com/SagerNet/sing-box/blob/v1.14.4/LICENSE). No upstream engine source or binaries are copied into this application.
