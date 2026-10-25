# khroxy security boundary (development preview)

Do not treat the Docker gateway as production-validated. Python unit tests and static checks are **not** proof of network isolation.

- Do not publish `~/.config/khroxy/state.json`, `runtime.json`, subscription URLs, tokens, or a copied `docker/xray` binary.
- Only expose the proxy to explicitly authorized containers and the Debian host loopback; the HTTP endpoint does **not** authenticate clients.
- Docker may modify **host** firewall rules. This project must not be deployed on khomelab until the owner approves a safe Docker/firewall change and SSH continuity testing.
- The AI process network policy is designed to **block direct egress**; it is not a transparent VPN. Proxy-unaware programs fail rather than bypass it.
- Public proxy access and DNS over IPv6 must be checked under real Docker namespaces. A dead Xray process must not cause direct fallback.
- `@fastest` is based on recent HTTPS measurements to a single test endpoint; it does not guarantee fastest ChatGPT/other AI endpoints. The host CLI currently requires manually initiated retesting, while the Docker preview includes scheduled probes.
- The Python runtime and Docker startup scripts are reviewed offline only; deployed firewall/killswitch tests remain a release gate.
- Report security issues privately; avoid including subscription URLs, UUIDs, proxy server credentials, account IDs, or app-specific secrets in issues.
