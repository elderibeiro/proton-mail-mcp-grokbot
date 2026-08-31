"""Allowlist / block policy for the Proton Mail MCP stdio wrapper.

Mirrors the sethbang harden.ts gates so tools/list and tools/call are filtered
even if the Node server is restarted without the patched build.
"""
from __future__ import annotations

SEND_TOOLS = frozenset({"send_email", "reply_email", "reply_all_email", "forward_email"})

READ_TOOLS = frozenset(
    {
        "list_folders",
        "list_messages",
        "read_message",
        "list_attachments",
        "download_attachment",
        "search_messages",
        "get_thread",
        "count_messages",
        "folder_stats",
        "top_senders",
    }
)

MUTATING_TOOLS = frozenset(
    {
        "send_email",
        "reply_email",
        "reply_all_email",
        "forward_email",
        "save_draft",
        "move_message",
        "delete_message",
        "update_message_flags",
        "mark_all_read",
        "bulk_move",
        "bulk_delete",
        "bulk_update_flags",
        "create_folder",
        "create_label",
        "rename_folder",
        "delete_folder",
        "update_message_labels",
        "bulk_update_labels",
        "empty_folder",
        "move_thread",
        "delete_thread",
        "flag_thread",
    }
)

WRITE_TOOLS_NEED_FOLDER = frozenset(
    {
        "move_message",
        "delete_message",
        "update_message_flags",
        "mark_all_read",
        "bulk_move",
        "bulk_delete",
        "bulk_update_flags",
        "create_folder",
        "rename_folder",
        "delete_folder",
        "update_message_labels",
        "bulk_update_labels",
        "empty_folder",
        "move_thread",
        "delete_thread",
        "flag_thread",
    }
)

READ_TOOLS_NEED_FOLDER = frozenset(
    {
        "list_messages",
        "read_message",
        "list_attachments",
        "download_attachment",
        "search_messages",
        "count_messages",
        "folder_stats",
        "top_senders",
        "get_thread",
    }
)

FOLDER_ARG_KEYS = ("folder", "folders", "mailbox", "destination", "path")

SYSTEM_ALIASES = {
    "inbox": "inbox",
    "drafts": "drafts",
    "draft": "drafts",
    "sent": "sent",
    "sent mail": "sent",
    "starred": "starred",
    "flagged": "starred",
    "star": "starred",
    "archive": "archive",
    "all mail": "all mail",
    "allmail": "all mail",
    "trash": "trash",
    "deleted": "trash",
    "deleted items": "trash",
    "junk": "junk",
    "spam": "junk",
}

SYSTEM_CANON = {
    "inbox",
    "drafts",
    "sent",
    "starred",
    "archive",
    "all mail",
    "trash",
    "junk",
}

DEFAULT_ALLOWED_ACTIONS = (
    "save_draft,update_message_flags,mark_all_read,move_message,update_message_labels"
)

# "*" or "ALL" disables the folder gate (read every mailbox/label).
# A comma list still works when you want a tight scope.
DEFAULT_ALLOWED_FOLDERS = "*"


def parse_comma_list(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    items = [s.strip() for s in raw.split(",") if s.strip()]
    if not items:
        return None
    if any(i == "*" or i.upper() == "ALL" for i in items):
        return None  # unrestricted
    return items


def env_true(name: str, environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else __import__("os").environ
    return env.get(name) == "true"


def normalize_folder_key(folder: str) -> str:
    s = folder.strip().lower().replace("\\", "/")
    if s.startswith("inbox.") and "/" not in s:
        s = "inbox/" + s[len("inbox.") :]
    s = s.replace(".", "/")
    if s in SYSTEM_ALIASES:
        s = SYSTEM_ALIASES[s]
    if s.startswith("inbox/"):
        rest = s[len("inbox/") :]
        aliased = SYSTEM_ALIASES.get(rest, rest)
        if aliased in SYSTEM_CANON:
            s = aliased
    if s in SYSTEM_ALIASES:
        s = SYSTEM_ALIASES[s]
    return s


def is_unrestricted_folders(allow_list: list[str] | None) -> bool:
    if not allow_list:
        return True
    return any(a == "*" or a.upper() == "ALL" for a in allow_list)


def is_folder_allowed(folder: str, allow_list: list[str] | None) -> bool:
    if is_unrestricted_folders(allow_list):
        return True
    key = normalize_folder_key(folder)
    return any(normalize_folder_key(a) == key for a in allow_list)


def keep_listed_folder(path: str, allow_list: list[str] | None) -> bool:
    if is_unrestricted_folders(allow_list):
        return True
    key = normalize_folder_key(path)
    if any(normalize_folder_key(a) == key for a in allow_list):
        return True
    return any(normalize_folder_key(a).startswith(key + "/") for a in allow_list)


def may_register_tool(
    name: str,
    *,
    readonly: bool,
    allow_send: bool,
    allowed_actions: list[str] | None,
) -> bool:
    if name in READ_TOOLS:
        return True
    if readonly and name in MUTATING_TOOLS:
        return False
    if name in SEND_TOOLS and not allow_send:
        return False
    if allowed_actions is not None and name in MUTATING_TOOLS and name not in allowed_actions:
        return False
    return True


def is_hidden_tool(name: str, environ: dict[str, str] | None = None) -> bool:
    import os

    env = environ if environ is not None else os.environ
    return not may_register_tool(
        name,
        readonly=env.get("READONLY") == "true",
        allow_send=env.get("PROTONMAIL_ALLOW_SEND") == "true",
        allowed_actions=parse_comma_list(env.get("PROTONMAIL_ALLOWED_ACTIONS")),
    )


def apply_call_guards(
    name: str,
    arguments: dict,
    environ: dict[str, str] | None = None,
) -> str | None:
    """Mutate arguments in place. Return an error message if the call must be blocked."""
    import os

    env = environ if environ is not None else os.environ
    if is_hidden_tool(name, env):
        return f"Unknown tool: {name}"

    if name == "read_message":
        arguments["preferHtml"] = False
    if name in ("list_messages", "search_messages"):
        arguments["includeSnippet"] = False

    allow = parse_comma_list(env.get("PROTONMAIL_ALLOWED_FOLDERS"))
    if allow is None:
        return None

    # Default omitted folder on reads; reject omitted folder on writes (save_draft has none).
    if name in READ_TOOLS_NEED_FOLDER and not arguments.get("folder") and name != "get_thread":
        arguments["folder"] = "INBOX"
    if name == "get_thread" and not arguments.get("messageId") and not arguments.get("folder"):
        arguments["folder"] = "INBOX"
    if name in WRITE_TOOLS_NEED_FOLDER:
        if name in ("move_message", "bulk_move", "move_thread") and not arguments.get("destination"):
            return "destination folder is required"
        if name in ("create_folder", "rename_folder", "delete_folder") and not arguments.get("path") and not arguments.get("folder"):
            return "folder path is required"

    def check_one(value: object, label: str) -> str | None:
        if isinstance(value, str):
            if not is_folder_allowed(value, allow):
                return f"Folder not in PROTONMAIL_ALLOWED_FOLDERS: {value}"
        elif isinstance(value, list):
            kept = [v for v in value if isinstance(v, str) and is_folder_allowed(v, allow)]
            if not kept and value:
                return f"No allowed folders in {label}"
            arguments[label] = kept
        return None

    for key in FOLDER_ARG_KEYS:
        if key not in arguments:
            continue
        err = check_one(arguments[key], key)
        if err:
            return err
    return None
