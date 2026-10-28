#!/bin/sh
# Workspace entrypoints use the configured engine; never invoke sudo.
set -eu
cd "$(dirname "$0")"
[ -f workspace-settings.json ] || { echo 'First run: python3 setup.py' >&2; exit 1; }
engine=$(python3 -c 'import json; e=json.load(open("workspace-settings.json")).get("engine","podman"); assert e in ("podman","docker"); print(e)')
command -v "$engine" >/dev/null 2>&1 || { echo 'Configured container engine not installed' >&2; exit 1; }
name=khroxy-ai
check_isolation() {
    # A direct-connect smoke test alone is NOT enough; check DROP first.
    # Shared verifier waits for startup, then checks both policies and agent probes.
    PYTHONDONTWRITEBYTECODE=1 python3 setup.py --engine "$engine" --verify-existing
}
case "${1:-}" in
 skills)
    shift
    exec "$engine" exec --user agent --env HOME=/home/agent "$name" python3 /usr/local/bin/caveman.py "$@"
    ;;
 tui)
    check_isolation || { echo "FAIL: isolation unverified; refusing access" >&2; exit 1; }
    shift
    exec "$engine" exec -it --user khroxy --env HOME=/home/khroxy "$name" khroxy "$@"
    ;;
 shell)
    check_isolation || { echo "FAIL: isolation unverified; refusing access" >&2; exit 1; }
    shift
    exec "$engine" exec -it --user agent --env HOME=/home/agent --workdir /workspace "$name" bash "$@"
    ;;
 codex)
    check_isolation || { echo "FAIL: isolation unverified; refusing access" >&2; exit 1; }
    shift
    exec "$engine" exec -it --user agent --env HOME=/home/agent --workdir /workspace "$name" codex "$@"
    ;;
 login)
    check_isolation || { echo "FAIL: isolation unverified; refusing access" >&2; exit 1; }
    shift
    exec "$engine" exec -it --user agent --env HOME=/home/agent --workdir /workspace "$name" codex login "$@"
    ;;
 verify)
    shift
    check_isolation
    ;;
 status)
    shift
    exec "$engine" ps --all --filter "name=^${name}$" "$@"
    ;;
 logs)
    shift
    exec "$engine" logs --tail 120 "$name" "$@"
    ;;
 stop)
    shift
    exec "$engine" stop "$name" "$@"
    ;;
 start)
    shift
    echo 'Existing stopped container only; use setup.py to build/create.' >&2
    "$engine" start "$name" "$@"
    if ! check_isolation; then
        echo 'FAIL: startup/isolation unverified; stopping container, volumes preserved' >&2
        "$engine" stop --time 3 "$name" || true
        exit 1
    fi
    echo 'Isolation smoke checks passed; full live security approval still required.'
    ;;
 *)
    echo 'Usage: ./workspace.sh {skills|tui|shell|codex|login|verify|status|logs|stop|start} [arguments...]' >&2
    exit 2
    ;;
esac
