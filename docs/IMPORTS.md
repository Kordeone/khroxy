# Import formats and ownership

The existing hidden `khroxy import NAME` prompt remains available. Expanded imports use `khroxy import-data --file /private/nodes.txt --source work` or pipe text on stdin. Credentials do not need to appear in process arguments. No URL found in an imported file is fetched. Subscription fetching remains the explicit `sub-add`/`sub-update` workflow with existing TLS-verified direct/profile/any retrieval rules.

Inputs are bounded to 1 MiB and 100 profiles: multiline share links, strict standard/URL-safe base64 lists, SIP008 JSON, individual Xray outbound objects, or Xray JSON containing `outbounds`. Xray imports extract outbounds only: input listeners/routing/controllers are not installed. Unsupported upstream options fail rather than being silently removed. A malformed batch is parsed before any state write and leaves the original state untouched.

VLESS, VMess, Trojan and Shadowsocks retain their original parsing and TLS/REALITY/ECH behavior. New link schemes are `http`, `https`, `socks5`/`socks`, and `hy2`/`hysteria2`. HTTP and SOCKS without TLS require `--allow-plaintext`; HTTPS and Hysteria2 verify TLS. Hysteria2 salamander obfuscation and SNI/ALPN are retained. Insecure TLS, unsupported parameters, nonempty SIP008 plugins and opaque network overrides are rejected. Engine-specific runtime compatibility is validated by T04; storing a profile does not by itself establish that the current engine supports it.

```sh
khroxy import-data --file /private/nodes.txt --source work
khroxy import-data --file /private/trusted-local-socks.txt --allow-plaintext
```

Import summaries contain counts only. Node identities are deterministic fingerprints excluding source labels, so repeated imports reuse configurations. Source-to-profile associations remain recorded. Subscription updates reuse existing identical nodes while retaining each source's ownership. Removing one subscription cannot delete another source's node or a reused manual profile. Existing profile names and application mappings remain unchanged.

A test previously using Hysteria2 as its unsupported-protocol fixture now uses an explicitly unknown scheme. The rejection contract remains covered; new Hysteria2 acceptance has separate tests. Real-engine protocol validation/traffic tests are recorded under T04 rather than inferred from parser success.
