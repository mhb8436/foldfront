// Per-user namespacing for the chatbot's browser storage.
// On a shared browser/PC multiple RAPID accounts must NOT see each other's
// conversations, attachment session, or provider config (which holds commercial
// API keys). This mirrors the run_prefix / buildUserPrefix scoping already used
// for homeContext and Workflow Studio storage keys.
//
// The resolved suffix is kept in module state and applied by scopedKey() at CALL
// time, so the chat libs stay pure and pick up the current account on each read.

import { buildUserPrefix } from "./pipeline.js";

// Empty or "default" => use the bare base key (anonymous stays stable and shares
// nothing with any signed-in account beyond the pre-existing global data we leave
// orphaned on purpose).
let currentSuffix = "";

// Resolve a per-user suffix: prefer the explicit run_prefix, else derive one from
// the username via buildUserPrefix, else "default" for anonymous / no user.
export function chatScopeSuffix(user = null) {
  if (!user || typeof user !== "object" || Array.isArray(user)) return "default";
  const explicit = String(user.run_prefix || "").trim();
  if (explicit) return explicit;
  const uname = String(user.username || "").trim();
  if (uname) return buildUserPrefix({ name: uname });
  return "default";
}

export function setChatScope(user = null) {
  const suffix = chatScopeSuffix(user);
  currentSuffix = suffix === "default" ? "" : suffix;
  return currentSuffix;
}

export function scopedKey(baseKey) {
  return currentSuffix ? `${baseKey}::${currentSuffix}` : baseKey;
}
