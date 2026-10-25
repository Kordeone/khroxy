#!/bin/sh
# Rootless Podman workspace entrypoints; never sudo and never touch Docker.
set -eu
cd "$(dirname "$0")"
[ -f workspace-settings.json ] || { echo 'First run: python3 setup.py' >&2; exit 1; }
command -v podman >/dev/null 2>&1 || { echo 'Podman not installed' >&2; exit 1; }
name=khroxy-ai
check_isolation() {
    # A direct-connect smoke test alone is NOT enough; check DROP first.
    podman exec --user root "$name" iptables -S OUTPUT | grep -Fq -- '-P OUTPUT DROP' || return 1
    podman exec --user root "$name" ip6tables -S OUTPUT | grep -Fq -- '-P OUTPUT DROP' || return 1
    podman exec --user agent --env HOME=/home/agent "$name" python3 /usr/local/bin/verify-isolation.py
}
case "${1:-}" in
 tui)
    shift
    exec podman exec -it --user khroxy --env HOME=/home/khroxy "$name" khroxy "$@"
    ;;
 shell)
    shift
    exec podman exec -it --user agent --env HOME=/home/agent --workdir /workspace "$name" bash "$@"
    ;;
 codex)
    shift
    exec podman exec -it --user agent --env HOME=/home/agent --workdir /workspace "$name" codex "$@"
    ;;
 login)
    shift
    exec podman exec -it --user agent --env HOME=/home/agent --workdir /workspace "$name" codex login "$@"
    ;;
 verify)
    shift
    check_isolation
    ;;
 status)
    shift
    exec podman ps --all --filter "name=^${name}$" "$@"
    ;;
 logs)
    shift
    exec podman logs --tail 120 "$name" "$@"
    ;;
 stop)
    shift
    exec podman stop "$name" "$@"
    ;;
 start)
    shift
    echo 'Existing stopped container only; use setup.py to build/create.' >&2
    podman start "$name" "$@"
    ready=no
    for n in 1 2 3 4 5 6 7 8 9 10; do
        if podman exec --user root "$name" iptables -S OUTPUT 2>/dev/null | grep -Fq -- '-P OUTPUT DROP' &&
           podman exec --user root "$name" ip6tables -S OUTPUT 2>/dev/null | grep -Fq -- '-P OUTPUT DROP'; then
            ready=yes
            break
        fi
        sleep 1
    done
    if [ "$ready" != yes ] || ! check_isolation; then
        echo 'FAIL: refusing unverified start, stopping container' >&2
        podman stop --time 3 "$name" || true
        exit 1
    fi
    echo 'Isolation smoke checks passed; full live security approval still required.'
    ;;
 *)
    echo 'Usage: ./workspace.sh {tui|shell|codex|login|verify|status|logs|stop|start} [arguments...]' >&2
    exit 2
    ;;
esac
