// Pure-ish helpers for chat attachments. FileReader-based reading lives in app.js;
// these are the testable formatting/session bits.

import { scopedKey } from "./chat-scope.js";

const SESSION_KEY = "rapid.chat.session.v1";

export function getChatSessionId() {
  try {
    const key = scopedKey(SESSION_KEY);
    let id = localStorage.getItem(key);
    if (!id) {
      id = (globalThis.crypto && globalThis.crypto.randomUUID)
        ? globalThis.crypto.randomUUID()
        : `s-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
      localStorage.setItem(key, id);
    }
    return id;
  } catch (_e) {
    return "default";
  }
}

// Rotate the attachment session so a new conversation starts with a clean
// server-side upload set. Without this, previously-uploaded files (e.g. an old
// 4KL5.pdb) keep getting injected into every later turn — even a brand-new chat
// with nothing attached.
export function resetChatSessionId() {
  try {
    localStorage.removeItem(scopedKey(SESSION_KEY));
  } catch (_e) {
    /* non-fatal */
  }
  return getChatSessionId();
}

export function attachmentChipLabel(a) {
  const kb = Math.round(((a && a.size) || 0) / 1024);
  return `${(a && a.name) || "file"} (${kb} KB)`;
}

export function withAttachments(payload, attachments, sessionId) {
  return {
    ...payload,
    attachments: Array.isArray(attachments) ? attachments : [],
    session_id: sessionId || "",
  };
}
