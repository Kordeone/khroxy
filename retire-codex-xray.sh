#!/usr/bin/env bash
# Explicit, user-scoped retirement of the OLD Codex-specific proxy.
# Leaves ~/.local/bin/xray (shared by khroxy) untouched.
set -euo pipefail

unit="$HOME/.config/systemd/user/codex-xray.service"
config="$HOME/.config/codex-xray/config.json"
switcher="$HOME/.local/bin/xray-switch"
status="$HOME/.local/bin/xray-status"
paths=("$unit" "$config" "$switcher" "$status")

# xray-status was intentionally installed as a symlink to xray-switch in
# older Codex setups. Only this exact owned symlink may be unlinked.
for path in "$unit" "$config" "$switcher"; do
  if [[ -L "$path" ]]; then
    printf 'Refusing symlink: %s\n' "$path" >&2
    exit 1
  fi
done
if [[ -L "$status" ]]; then
  target=$(readlink -- "$status")
  if [[ "$target" != 'xray-switch' && "$target" != "$switcher" ]]; then
    printf 'Refusing unknown xray-status symlink target; inspect manually.\n' >&2
    exit 1
  fi
fi

installed=$(systemctl --user show -P FragmentPath codex-xray.service 2>/dev/null || true)
if [[ -n "$installed" && "$installed" != "$unit" ]]; then
  printf 'Found Codex unit outside expected user path: %s\n' "$installed" >&2
  printf 'Nothing changed; inspect the unit manually.\n' >&2
  exit 1
fi

printf '%s\n' 'Old Codex proxy retirement (current user only):'
printf '%s\n' '  Stop and disable codex-xray.service'
for path in "${paths[@]}"; do
  printf '  Remove (if present): %s\n' "$path"
done
printf '%s\n' '  Preserve: ~/.local/bin/xray, khroxy, firewall, SSH, Windscribe, NetworkManager'
printf '%s\n' '  Any separately configured proxy environment variables are NOT changed.'
if [[ "${1:---dry-run}" != '--apply' ]]; then
  printf '\n%s\n' 'DRY RUN. Execute with --apply to perform the above removal.'
  exit 0
fi
read -r -p 'Type REMOVE CODEX-XRAY to confirm: ' confirm
[[ "$confirm" == 'REMOVE CODEX-XRAY' ]] || { printf 'Cancelled.\n'; exit 1; }
if [[ -e "$unit" || -n "$installed" ]]; then
  systemctl --user disable --now codex-xray.service
  if systemctl --user is-active --quiet codex-xray.service; then
    printf 'Codex proxy still running, not removing files.\n' >&2
    exit 1
  fi
fi
for path in "${paths[@]}"; do
  if [[ -e "$path" || -L "$path" ]]; then
    rm -- "$path"  # unlink only: never follow the approved symlink
  fi
done
rmdir "$HOME/.config/codex-xray" 2>/dev/null || true
systemctl --user daemon-reload
printf 'Old codex-xray user service/config/tools retired. Xray binary preserved.\n'
