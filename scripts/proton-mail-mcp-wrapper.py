#!/usr/bin/env python3
"""Launch sethbang proton-mail-mcp over stdio. Secrets come from `pass`, not argv."""
import os, subprocess, sys

NODE_BIN = os.environ.get("NODE_BIN", os.path.expanduser("~/.local/node-v24.20.0-linux-x64/bin"))
MCP_JS = os.environ.get("MCP_JS", os.path.expanduser("~/.local/opt/proton-mail-mcp/build/index.js"))
PASS_KEY = "proton-bridge/imap"

os.environ["PATH"] = NODE_BIN + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("READONLY", "false")
if not os.environ.get("PROTONMAIL_USERNAME"):
    sys.stderr.write("proton-mail-mcp-wrapper: set PROTONMAIL_USERNAME\n")
    sys.exit(2)
os.environ.setdefault("IMAP_HOST", "127.0.0.1")
os.environ.setdefault("IMAP_PORT", "1143")
os.environ.setdefault("IMAP_SECURE", "false")
os.environ.setdefault("PROTONMAIL_HOST", "127.0.0.1")
os.environ.setdefault("PROTONMAIL_PORT", "1025")
os.environ.setdefault("PROTONMAIL_SECURE", "false")
os.environ.setdefault("ALLOW_EMPTY_FOLDER", "false")
os.environ.setdefault("ALLOW_FILE_DOWNLOAD_DIR", os.path.expanduser("~/.local/share/proton-mcp-attachments"))

if not os.environ.get("IMAP_PASSWORD") and not os.environ.get("PROTONMAIL_PASSWORD"):
    try:
        pw = subprocess.check_output(["pass", PASS_KEY], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.stderr.write(
            "proton-mail-mcp-wrapper: no IMAP password. After Bridge login, "
            "store it with:  pass insert -e proton-bridge/imap\n"
        )
        sys.exit(2)
    os.environ["IMAP_PASSWORD"] = pw
    os.environ["PROTONMAIL_PASSWORD"] = pw

os.makedirs(os.environ["ALLOW_FILE_DOWNLOAD_DIR"], mode=0o700, exist_ok=True)
os.execv(os.path.join(NODE_BIN, "node"), ["node", MCP_JS])
