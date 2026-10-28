# Bounded concurrent profile testing

Legacy `ping`, `ping --all` and application `test APP` retain their interfaces. The additive command tests profiles through owned temporary engine processes, without replacing application runtime files or restarting the active proxy:

```sh
khroxy test-profiles --url https://your-approved-test.example/generate_204
khroxy test-profiles main spare --stages tcp,http --concurrency 2 --timeout 10 --retries 1 --url https://your-approved-test.example/test
khroxy test-profiles main --stages download --max-bytes 1048576 --url https://your-approved-test.example/download
khroxy test-profiles main --stages upload --max-bytes 1048576 --url https://your-approved-test.example/upload
```

The example endpoints are placeholders. Use an endpoint you are authorized to test. No endpoint is discovered or contacted automatically by this command. HTTP(S) userinfo and fragments are rejected. HTTPS verifies certificates; `--ca-file` explicitly trusts a local CA without turning verification off.

Stages are optional: `icmp`, `tcp`, `http`, `download`, `upload`. ICMP/TCP describe the upstream endpoint, not successful proxied Internet access. QUIC-based profiles may work while their TCP port is closed. A successful proxied HTTP stage establishes measured availability; a TCP/ICMP-only run reports availability as null, not healthy. Missing ping support is reported as unsupported, without requesting privileges.

Limits: 1–32 workers, 0.1–120 seconds per profile across startup/stages/retries, 0–3 retries per stage, 1–8388608 payload bytes per profile, and at most 1000 profiles per invocation. The byte budget is shared across stages and retries. HTTP protocol/engine overhead is additional; the payload cap bounds bytes read/sent by the tester, not every packet buffered by the kernel. A receiver ignoring Range cannot make the tester read an unlimited body. Uploads are streamed in bounded chunks.

DNS resolution uses a bounded owned helper process; slow headers/bodies have an independent deadline watchdog. Ctrl+C cancels queued work, closes owned sockets and terminates/reaps owned test processes, returning exit 130 with structured cancellation results. Engine shutdown adds bounded cleanup time after a profile deadline. No foreign process or application proxy is terminated.

JSON results identify each stage's outcome, attempt count, measured delay, payload count and failure category. Raw engine output, credentials, input URLs and response bodies are not included. TLS, DNS, refused connections, timeouts, HTTP status/protocol errors, unsupported ICMP, exhausted byte budgets and cancellation are distinguished. Results from these local fixtures are not live Internet speed or container-isolation verification.
