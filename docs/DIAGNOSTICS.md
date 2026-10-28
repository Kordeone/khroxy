# Persistent results, freshness and optional metadata

Advanced tests require migrated SQLite storage; new installs already have it. On a legacy JSON-only installation, stop older KHROXY writers and run `khroxy storage migrate` before using persistent tests. This avoids silently creating a second authoritative state while an old process still writes JSON. If an old writer later changes the preserved JSON, new state reads/writes report a conflict and retain both sources; `storage backup` can still recover the database. Reconcile only after stopping the old writer and protecting both copies.

Every advanced test result and native legacy health probe is recorded privately with its configuration fingerprint and timestamp. Histories contain categories/measurements, not URLs, response bodies, credentials or raw engine output. A result for an edited configuration is not treated as availability of the new configuration.

```sh
khroxy results rank
khroxy results rank --sort download --max-age 300
khroxy results rank --sort upload --include-disabled
khroxy storage history --profile main --limit 50 --offset 0
khroxy results prune --before 1700000000 --keep 10000
```

Ranking distinguishes healthy, failed, stale and untested. TCP/ICMP-only results do not become proxied availability. Time in the future or older than `--max-age` is stale. Failures retain their categories (TLS, DNS, timeout, HTTP status, etc.). Sorting prioritizes fresh healthy results, then measurable delay or throughput; missing measurements are not zero. Retention removes old/excess history transactionally, not profiles or credentials. Pruning is explicit rather than silent deletion.

Optional country/ASN enrichment is entirely offline. Supply a reviewed JSON CIDR dataset; KHROXY bundles no location database, performs no metadata network/DNS requests and does not infer exit geography from endpoint geography:

```json
{"version":1,"networks":[{"cidr":"192.0.2.0/24","country":"ZZ","asn":64500}]}
```

The example data are fictional. Run `khroxy metadata enrich --file /trusted/geo.json`. Datasets are bounded to 4 MiB and 10000 CIDRs. The most specific prefix wins. Domain-only endpoints remain unenriched rather than triggering an unexpected resolver lookup. Missing/invalid datasets report metadata unavailable without changing proxy configuration. Stored metadata timestamps and provenance remain separate from connection health.

No new third-party runtime dependency is introduced. Data-provider accuracy and licensing remain the responsibility of whoever supplies the optional dataset; it is not included in release artifacts.
