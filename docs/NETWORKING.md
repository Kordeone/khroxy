# Local listeners, routing and DNS

Existing application HTTP ports and mappings are unchanged. Additional services are opt-in CLI features; the original TUI is unchanged. Stop the managed proxy deliberately before editing networking. KHROXY refuses to restart active sessions automatically for these changes.

```
khroxy listener add tools --protocol socks --port 21080 --profile @fastest
khroxy listener add web --protocol http --port 21081 --profile my-profile
khroxy listener list
```

Only explicit `127.0.0.1` and `::1` listeners are supported. Ports below 1024, reserved gateway ports, application/listener/control collisions and public addresses are rejected. SOCKS5 UDP relay is disabled. Optional HTTP/SOCKS authentication uses `--auth-stdin`: provide a JSON object containing `username` and `password` through private stdin, not shell arguments/history. Output never includes the password. Authentication protects access, not transport encryption; loopback remains mandatory. All local processes with access to an unauthenticated loopback port can use it.

Shadowsocks uses `--protocol shadowsocks --method aes-128-gcm --auth-stdin` and requires authentication. Supported AEAD methods: aes-128-gcm, aes-256-gcm, chacha20-ietf-poly1305. The username field is validated for a consistent input format but Shadowsocks uses only the password. Persisted authentication is private and survives rebuilding/upgrading. `listener remove NAME` removes configuration, never projects or volumes.

Rules are evaluated in insertion order, before the original per-application defaults. Different match fields combine with AND; exact/suffix domain alternatives combine with OR. CIDRs match literal destination IPs; KHROXY does not resolve hostnames merely to evaluate IP rules. Use `app-NAME` or `listener-NAME` to restrict an inbound. Names must exist. Without a matcher, `--all` is required explicitly.

```
khroxy route add deny-example --action block --domain example.invalid
khroxy route add project-local --action direct --cidr 192.0.2.0/24 --inbound app-tools
khroxy route add selected --action proxy --profile @active --suffix example.test
khroxy route list
khroxy route remove deny-example
```

`direct` is a deliberate rule, never an automatic fallback. New extension configurations put a blackhole first; sing-box rejects unmatched traffic. A failed proxy stays failed. Fixed profile assignments remain fixed; `@active` and `@fastest` retain their meanings and session-safe Fastest switching.

DNS configuration is engine-local, not a host resolver modification:

```
khroxy dns show
khroxy dns set static --hosts-file private-hosts.json
khroxy dns set system
```

The hosts file maps normalized DNS names to lists of literal IPv4/IPv6 addresses, for example `{"project.test":["192.0.2.10","2001:db8::10"]}`. `system` preserves the engine's ordinary resolver behavior and is not a DNS leak prevention mode. `static` resolves local direct destinations using only the configured map, with no system fallback. Missing names fail. Xray/V2Ray use a loopback-only failing DoH endpoint for misses; sing-box uses its native hosts resolver. Static mode requires proxy-server endpoints to be literal IPs; TLS SNI names are preserved. Names forwarded through a proxy may be resolved by the remote proxy, independently of this local setting. This is not host-wide DNS capture, a TUN, or protection for applications bypassing their configured proxy.

Behavior is tested using all three real engines, HTTP/SOCKS5 clients, a real Shadowsocks client, IPv4 and IPv6 loopback, static-resolution failure, and precedence/no-direct-fallback cases. Additional tests run in a disposable rootless network namespace with only loopback; no production host networking changes are needed. Engine DNS sources: [Xray](https://xtls.github.io/en/config/dns.html), [V2Ray](https://www.v2fly.org/en_US/config/dns.html), [sing-box hosts](https://sing-box.sagernet.org/configuration/dns/server/hosts/).
