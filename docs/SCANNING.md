# Explicit CDN/edge candidate testing

Scanning is opt-in TCP reachability, not CDN discovery, speed certification, censorship evasion validation or unrestricted address scanning. Provide only endpoints you are authorized to test. KHROXY never expands a network range or resolves a target hostname.

```
khroxy scan run project-edge --file candidates.json --concurrency 4 --rate 5 --timeout 2
khroxy scan results project-edge --limit 100 --offset 0
khroxy scan run project-edge --file candidates.json --resume
```

The file contains JSON objects: `[{"ip":"192.0.2.10","port":443},{"ip":"2001:db8::10","port":443}]`. These documentation addresses are not working test endpoints. Real tests use local fixture listeners. At most 1000 entries; duplicates normalize to one candidate. Hostnames, CIDRs, ranges, multicast, unspecified, scoped and broadcast destinations are rejected. Limits: 1–32 workers, 0.1–20 connection attempts/s, 0.1–10 s per target. Rate limiting spaces starts without a burst. Work duration depends on candidate count and configured rate.

Ctrl+C closes owned sockets, cancels queued work, retains completed checkpoints and returns 130. `--resume` requires the identical normalized candidate list and skips all completed targets, including failures. Use a new scan name to retest. A private lock prevents two processes running the same named scan. No application runtime, routing file or engine process is changed by scanning.

Results persist in SQLite metadata history and validated backups, including status/category, elapsed TCP connection latency and timestamp. `results` ranks successful targets by latency, then failures; an old success is a historical measurement, not proof of current availability. Candidate addresses remain explicit: exporting a profile or changing a proxy server to an edge address is a separate deliberate operation that must retain the server's TLS identity.
