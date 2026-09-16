// Browser-stored copilot conversation history (BMP-style). No DOM/network.
// A conversation: { id, title, messages:[{role:"user"|"ai", text}], updatedAt }.

import { scopedKey } from "./chat-scope.js";

const KEY = "rapid.chat.conversations.v1";
// Browser-wide one-time flag: set the first time a signed-in account runs the
// legacy carry-over below, so it fires EXACTLY ONCE (right after this feature
// ships) and never again — otherwise it would keep stealing a later anonymous
// user's bare-key chats into a signed-in account, breaking per-user isolation.
const LEGACY_MIGRATED_KEY = "rapid.chat.legacyMigrated.v1";
const MAX = 30;

// One-time carry-over. Before per-user namespacing, conversations lived under the
// bare KEY (shared across accounts on the same browser). The FIRST signed-in
// account to load after this ships inherits that legacy history into its own
// namespace (so the user keeps their chats); the global copy is then deleted and
// the flag set, so no other account inherits it and future anonymous/signed-in
// use stays fully isolated.
function migrateLegacyConversations() {
  try {
    const scoped = scopedKey(KEY);
    if (scoped === KEY) return; // anonymous: scoped IS the global key — never migrate
    if (localStorage.getItem(LEGACY_MIGRATED_KEY)) return; // one-time only
    const legacy = localStorage.getItem(KEY);
    if (legacy != null) {
      if (!localStorage.getItem(scoped)) localStorage.setItem(scoped, legacy);
      localStorage.removeItem(KEY);
    }
    localStorage.setItem(LEGACY_MIGRATED_KEY, "1");
  } catch (_e) {
    /* non-fatal */
  }
}

export function loadConversations() {
  migrateLegacyConversations();
  try {
    const raw = localStorage.getItem(scopedKey(KEY));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (_e) {
    return [];
  }
}

function persist(list) {
  try {
    const trimmed = [...list]
      .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
      .slice(0, MAX);
    localStorage.setItem(scopedKey(KEY), JSON.stringify(trimmed));
    return trimmed;
  } catch (_e) {
    return list;
  }
}

export function conversationTitle(messages) {
  const first = (Array.isArray(messages) ? messages : []).find((m) => m && m.role === "user");
  const t = String((first && first.text) || "").trim();
  return t ? t.slice(0, 40) : "New chat";
}

export function upsertConversation(id, messages, now) {
  const others = loadConversations().filter((c) => c.id !== id);
  const entry = {
    id,
    title: conversationTitle(messages),
    messages: Array.isArray(messages) ? messages : [],
    updatedAt: typeof now === "number" ? now : Date.now(),
  };
  return persist([entry, ...others]);
}

export function deleteConversation(id) {
  return persist(loadConversations().filter((c) => c.id !== id));
}

export function newConversationId() {
  return (globalThis.crypto && globalThis.crypto.randomUUID)
    ? globalThis.crypto.randomUUID()
    : `c-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
}
