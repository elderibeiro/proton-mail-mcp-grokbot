#!/bin/sh
# Interactive Proton Mail Bridge login. No secrets in this file.
set -eu

if [ -z "${PROTONMAIL_USERNAME:-}" ]; then
  echo "proton-bridge-login: set PROTONMAIL_USERNAME (your Proton account username)." >&2
  echo "Never pass an IMAP / Bridge password here." >&2
  exit 2
fi

HOME_DIR="${HOME:?HOME is not set}"
CONF="${SUPERVISOR_CONF:-$HOME_DIR/.config/supervisor/supervisord.conf}"

# Only one Bridge instance can hold the lock.
supervisorctl -c "$CONF" stop protonmail-bridge >/dev/null 2>&1 || true

cat >&2 <<EOF
proton-bridge-login: starting official Bridge CLI.

In the CLI, type these commands (never echo a password into a log):

  1. login
  2. Username: ${PROTONMAIL_USERNAME}
     (this is the value of PROTONMAIL_USERNAME — type it when prompted)
  3. Password: your Proton account password
     (NOT a Bridge / IMAP password)
  4. TOTP / 2FA if asked
  5. info
     Copy the IMAP password from the output. Do not paste it into chat or git.
  6. exit

Then open another terminal and store the IMAP password in pass (not argv, not git):

  pass insert -e proton-bridge/imap
  # paste the IMAP password, then Ctrl-D

Start Bridge again with:

  scripts/start-proton-stack.sh

EOF

exec /usr/bin/protonmail-bridge -c --log-level info
