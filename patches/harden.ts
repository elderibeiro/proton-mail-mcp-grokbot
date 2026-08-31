/**
 * Deployment harden layer for the local Proton Mail MCP.
 *
 * Gated by env (wrapper supplies defaults). When an env var is unset, folder
 * and action allowlists are inactive so unit tests keep the upstream surface;
 * ALLOW_SEND is fail-closed (missing/false → send-family tools are not registered).
 */
import { createHash } from "node:crypto";
import type { ConnectionOptions, PeerCertificate, TLSSocket } from "node:tls";

export const SEND_TOOLS = ["send_email", "reply_email", "reply_all_email", "forward_email"] as const;

export const READ_TOOLS = [
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
] as const;

/** Mailbox-mutating tools. save_draft is a write and stays when listed. */
export const MUTATING_TOOLS = [
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
] as const;

const SEND_SET = new Set<string>(SEND_TOOLS);
const READ_SET = new Set<string>(READ_TOOLS);
const MUTATING_SET = new Set<string>(MUTATING_TOOLS);

const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1"]);

const SYSTEM_ALIASES: Record<string, string> = {
  inbox: "inbox",
  drafts: "drafts",
  draft: "drafts",
  sent: "sent",
  "sent mail": "sent",
  starred: "starred",
  flagged: "starred",
  star: "starred",
  archive: "archive",
  "all mail": "all mail",
  allmail: "all mail",
  trash: "trash",
  deleted: "trash",
  "deleted items": "trash",
  junk: "junk",
  spam: "junk",
};

const SYSTEM_CANON = new Set([
  "inbox",
  "drafts",
  "sent",
  "starred",
  "archive",
  "all mail",
  "trash",
  "junk",
]);

export function parseCommaList(raw: string | undefined): string[] | null {
  if (raw === undefined) return null;
  const items = raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return items.length > 0 ? items : null;
}

export function allowSendEnabled(): boolean {
  return process.env.PROTONMAIL_ALLOW_SEND === "true";
}

export function getAllowedActions(): string[] | null {
  return parseCommaList(process.env.PROTONMAIL_ALLOWED_ACTIONS);
}

export function getAllowedFolders(): string[] | null {
  const items = parseCommaList(process.env.PROTONMAIL_ALLOWED_FOLDERS);
  if (!items) return null;
  if (items.some((i) => i === "*" || i.toUpperCase() === "ALL")) return null;
  return items;
}

export function mayRegisterTool(name: string): boolean {
  const readonly = process.env.READONLY === "true";
  if (READ_SET.has(name)) return true;
  if (readonly && MUTATING_SET.has(name)) return false;
  if (SEND_SET.has(name) && !allowSendEnabled()) return false;
  const actions = getAllowedActions();
  if (actions && MUTATING_SET.has(name) && !actions.includes(name)) return false;
  return true;
}

/**
 * Canonical folder key: case-insensitive, slash-normalized, INBOX.Drafts → drafts.
 */
export function normalizeFolderKey(folder: string): string {
  let s = folder.trim().toLowerCase().replace(/\\/g, "/");
  s = s.replace(/^inbox\./, "inbox/");
  s = s.replace(/\./g, "/");
  if (SYSTEM_ALIASES[s]) s = SYSTEM_ALIASES[s];
  if (s.startsWith("inbox/")) {
    const rest = s.slice("inbox/".length);
    const aliased = SYSTEM_ALIASES[rest] ?? rest;
    if (SYSTEM_CANON.has(aliased)) s = aliased;
  }
  if (SYSTEM_ALIASES[s]) s = SYSTEM_ALIASES[s];
  return s;
}

export function isFolderAllowed(folder: string, allowList: string[] | null = getAllowedFolders()): boolean {
  if (!allowList) return true;
  if (allowList.some((a) => a === "*" || a.toUpperCase() === "ALL")) return true;
  const key = normalizeFolderKey(folder);
  return allowList.some((a) => normalizeFolderKey(a) === key);
}

export function assertFolderAllowed(folder: string): void {
  if (!isFolderAllowed(folder)) {
    throw new Error(`Folder not in PROTONMAIL_ALLOWED_FOLDERS: ${folder}`);
  }
}

/** Keep allowlisted mailboxes plus ancestor prefixes so list_folders still shows a tree. */
export function keepListedFolder(path: string, allowList: string[] | null = getAllowedFolders()): boolean {
  if (!allowList) return true;
  if (allowList.some((a) => a === "*" || a.toUpperCase() === "ALL")) return true;
  const key = normalizeFolderKey(path);
  if (allowList.some((a) => normalizeFolderKey(a) === key)) return true;
  for (const a of allowList) {
    const ak = normalizeFolderKey(a);
    if (ak.startsWith(key + "/")) return true;
  }
  return false;
}

export function filterListedFolders<T extends { path: string }>(folders: T[], allowList: string[] | null = getAllowedFolders()): T[] {
  if (!allowList) return folders;
  return folders.filter((f) => keepListedFolder(f.path, allowList));
}

export function restrictFolderWalk(folders: readonly string[], allowList: string[] | null = getAllowedFolders()): string[] {
  if (!allowList) return [...folders];
  return folders.filter((f) => isFolderAllowed(f, allowList));
}

export function normalizeCertPin(pin: string | undefined): string | undefined {
  if (!pin) return undefined;
  const hex = pin.replace(/:/g, "").trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(hex)) {
    throw new Error("PROTONMAIL_BRIDGE_CERT_SHA256 must be a 64-character hex SHA-256 fingerprint");
  }
  return hex;
}

export function pinMismatchError(cert: { raw?: Buffer } | PeerCertificate, expected: string): Error | undefined {
  const raw = (cert as { raw?: Buffer }).raw;
  if (!raw) return new Error("Bridge TLS certificate pin: peer certificate missing");
  const actual = createHash("sha256").update(raw).digest("hex");
  if (actual !== expected) return new Error("Bridge TLS certificate pin mismatch");
  return undefined;
}

export function makeBridgeTlsOptions(host: string, pin?: string): ConnectionOptions | undefined {
  const loopback = LOOPBACK.has(host);
  const expected = pin ? normalizeCertPin(pin) : undefined;
  if (!loopback && !expected) return undefined;
  const opts: ConnectionOptions = {};
  if (loopback) opts.rejectUnauthorized = false;
  if (expected) {
    opts.checkServerIdentity = (_servername, cert) => pinMismatchError(cert, expected) ?? undefined;
  }
  return opts;
}

export function assertPeerCertPin(client: object, pin?: string): void {
  const expected = pin ? normalizeCertPin(pin) : undefined;
  if (!expected) return;
  const rec = client as Record<string, unknown>;
  let sock: TLSSocket | undefined;
  for (const k of ["socket", "_socket", "stream", "_stream"]) {
    const v = rec[k];
    if (v && typeof (v as TLSSocket).getPeerCertificate === "function") {
      sock = v as TLSSocket;
      break;
    }
  }
  if (!sock) {
    throw new Error("Bridge TLS certificate pin: connection is not TLS");
  }
  const cert = sock.getPeerCertificate(true);
  const err = pinMismatchError(cert, expected);
  if (err) throw err;
}
