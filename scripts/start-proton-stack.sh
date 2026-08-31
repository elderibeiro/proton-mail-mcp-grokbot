#!/bin/sh
# Keep Proton Mail Bridge up via user supervisord. No secrets here.
set -eu
HOME_DIR="${HOME:?HOME is not set}"
CONF="${SUPERVISOR_CONF:-$HOME_DIR/.config/supervisor/supervisord.conf}"
SOCK="${SUPERVISOR_SOCK:-$HOME_DIR/.local/var/run/supervisor.sock}"
export HOME="$HOME_DIR"
export USER="${USER:-$(id -un)}"
export PATH="/usr/bin:/bin:$HOME_DIR/.local/bin"
mkdir -p "$HOME_DIR/.local/var/run" "$HOME_DIR/.local/var/log"
if [ -S "$SOCK" ] && supervisorctl -c "$CONF" pid >/dev/null 2>&1; then
  supervisorctl -c "$CONF" start protonmail-bridge >/dev/null 2>&1 || true
  exit 0
fi
supervisord -c "$CONF"
