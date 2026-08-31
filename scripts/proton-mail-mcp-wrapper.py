#!/usr/bin/env python3
"""Launch sethbang proton-mail-mcp over stdio with a JSON-RPC allowlist proxy.

Secrets come from `pass`, never argv. MCP SDK v2 frames stdio as NDJSON
(JSON line + newline), not LSP Content-Length — verified against
@modelcontextprotocol/server serializeMessage / ReadBuffer.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
from typing import Any, BinaryIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proton_mail_mcp_policy import (  # noqa: E402
    DEFAULT_ALLOWED_ACTIONS,
    DEFAULT_ALLOWED_FOLDERS,
    apply_call_guards,
    is_hidden_tool,
)

NODE_BIN = os.environ.get("NODE_BIN", os.path.expanduser("~/.local/node-v24.20.0-linux-x64/bin"))
MCP_JS = os.environ.get("MCP_JS", os.path.expanduser("~/.local/opt/proton-mail-mcp/build/index.js"))
PASS_KEY = "proton-bridge/imap"
MAX_LINE = 10 * 1024 * 1024


def _setup_env() -> None:
    os.environ["PATH"] = NODE_BIN + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("READONLY", "false")
    os.environ.setdefault("PROTONMAIL_ALLOW_SEND", "false")
    os.environ.setdefault("PROTONMAIL_ALLOWED_ACTIONS", DEFAULT_ALLOWED_ACTIONS)
    os.environ.setdefault("PROTONMAIL_ALLOWED_FOLDERS", DEFAULT_ALLOWED_FOLDERS)
    os.environ.setdefault("IMAP_HOST", "127.0.0.1")
    os.environ.setdefault("IMAP_PORT", "1143")
    os.environ.setdefault("IMAP_SECURE", "false")
    os.environ.setdefault("PROTONMAIL_HOST", "127.0.0.1")
    os.environ.setdefault("PROTONMAIL_PORT", "1025")
    os.environ.setdefault("PROTONMAIL_SECURE", "false")
    os.environ.setdefault("ALLOW_EMPTY_FOLDER", "false")
    os.environ.setdefault("RESTRICT_OUTBOUND_TO_SELF", "false")
    os.environ.setdefault(
        "ALLOW_FILE_DOWNLOAD_DIR",
        os.path.expanduser("~/.local/share/proton-mcp-attachments"),
    )


def _require_username() -> None:
    if not os.environ.get("PROTONMAIL_USERNAME"):
        sys.stderr.write(
            "proton-mail-mcp-wrapper: PROTONMAIL_USERNAME is not set. "
            "The MCP registration must supply it; this wrapper no longer "
            "hardcodes an address.\n"
        )
        sys.exit(2)


def _load_password() -> None:
    if os.environ.get("IMAP_PASSWORD") or os.environ.get("PROTONMAIL_PASSWORD"):
        if os.environ.get("IMAP_PASSWORD") and not os.environ.get("PROTONMAIL_PASSWORD"):
            os.environ["PROTONMAIL_PASSWORD"] = os.environ["IMAP_PASSWORD"]
        elif os.environ.get("PROTONMAIL_PASSWORD") and not os.environ.get("IMAP_PASSWORD"):
            os.environ["IMAP_PASSWORD"] = os.environ["PROTONMAIL_PASSWORD"]
        return
    try:
        pw = subprocess.check_output(
            ["pass", PASS_KEY],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.stderr.write(
            "proton-mail-mcp-wrapper: no IMAP password. After Bridge login, "
            "store it with:  pass insert -e proton-bridge/imap\n"
        )
        sys.exit(2)
    os.environ["IMAP_PASSWORD"] = pw
    os.environ["PROTONMAIL_PASSWORD"] = pw


def _encode(msg: dict[str, Any]) -> bytes:
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _rpc_error(id_: Any, message: str, code: int = -32601) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _filter_tools_list(msg: dict[str, Any]) -> dict[str, Any]:
    result = msg.get("result")
    if not isinstance(result, dict):
        return msg
    tools = result.get("tools")
    if not isinstance(tools, list):
        return msg
    result["tools"] = [
        t
        for t in tools
        if not (isinstance(t, dict) and is_hidden_tool(str(t.get("name", ""))))
    ]
    return msg


def _handle_client_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Return a response to send back to the client without forwarding, or None to forward."""
    method = msg.get("method")
    if method != "tools/call":
        return None
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    name = str(params.get("name") or "")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
        params["arguments"] = arguments
        msg["params"] = params
    err = apply_call_guards(name, arguments)
    if err:
        return _rpc_error(msg.get("id"), err)
    return None


class LineReader:
    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        self.buf.extend(chunk)
        if len(self.buf) > MAX_LINE:
            raise RuntimeError("stdio JSON-RPC line exceeded 10 MiB")
        out: list[dict[str, Any]] = []
        while True:
            nl = self.buf.find(b"\n")
            if nl < 0:
                break
            line = self.buf[:nl]
            del self.buf[: nl + 1]
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line.strip():
                continue
            try:
                parsed = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        return out


def _proxy(child: subprocess.Popen[bytes]) -> int:
    assert child.stdin and child.stdout
    stdin_fd = sys.stdin.buffer.fileno()
    child_out_fd = child.stdout.fileno()
    child_err_fd = child.stderr.fileno() if child.stderr else None
    client_r = LineReader()
    child_r = LineReader()
    pending: dict[Any, str] = {}
    stdin_open = True

    while True:
        fds: list[int] = []
        if stdin_open:
            fds.append(stdin_fd)
        if child.stdout:
            fds.append(child_out_fd)
        if child_err_fd is not None:
            fds.append(child_err_fd)
        if not fds:
            break
        readable, _, _ = select.select(fds, [], [], 1.0)
        if not readable:
            if child.poll() is not None and not stdin_open:
                break
            if child.poll() is not None:
                # Drain remaining child stdout then exit.
                try:
                    rest = child.stdout.read() if child.stdout else b""
                except Exception:
                    rest = b""
                if rest:
                    for msg in child_r.feed(rest):
                        _emit_child_msg(msg, pending)
                break
            continue

        if stdin_fd in readable:
            chunk = os.read(stdin_fd, 65536)
            if not chunk:
                stdin_open = False
                try:
                    child.stdin.close()
                except Exception:
                    pass
            else:
                for msg in client_r.feed(chunk):
                    blocked = _handle_client_request(msg)
                    if blocked is not None:
                        sys.stdout.buffer.write(_encode(blocked))
                        sys.stdout.buffer.flush()
                        continue
                    mid = msg.get("id")
                    method = msg.get("method")
                    if mid is not None and isinstance(method, str):
                        pending[mid] = method
                    try:
                        child.stdin.write(_encode(msg))
                        child.stdin.flush()
                    except BrokenPipeError:
                        return child.wait() or 1

        if child_out_fd in readable:
            chunk = os.read(child_out_fd, 65536)
            if not chunk:
                if child.poll() is not None:
                    break
            else:
                for msg in child_r.feed(chunk):
                    _emit_child_msg(msg, pending)

        if child_err_fd is not None and child_err_fd in readable:
            chunk = os.read(child_err_fd, 65536)
            if chunk:
                sys.stderr.buffer.write(chunk)
                sys.stderr.buffer.flush()

        if child.poll() is not None and not stdin_open:
            # final drain
            try:
                rest = child.stdout.read() if child.stdout else b""
            except Exception:
                rest = b""
            if rest:
                for msg in child_r.feed(rest):
                    _emit_child_msg(msg, pending)
            break

    return child.wait() if child.poll() is None else child.returncode or 0


def _emit_child_msg(msg: dict[str, Any], pending: dict[Any, str]) -> None:
    mid = msg.get("id")
    method = pending.pop(mid, None) if mid is not None else msg.get("method")
    if method == "tools/list" and "result" in msg:
        msg = _filter_tools_list(msg)
    sys.stdout.buffer.write(_encode(msg))
    sys.stdout.buffer.flush()


def main() -> None:
    _setup_env()
    _require_username()
    _load_password()
    os.makedirs(os.environ["ALLOW_FILE_DOWNLOAD_DIR"], mode=0o700, exist_ok=True)

    node = os.path.join(NODE_BIN, "node")
    child = subprocess.Popen(
        [node, MCP_JS],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )

    def _forward_signal(signum: int, _frame: Any) -> None:
        try:
            child.send_signal(signum)
        except OSError:
            pass

    signal.signal(signal.SIGINT, _forward_signal)
    signal.signal(signal.SIGTERM, _forward_signal)

    try:
        rc = _proxy(child)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
