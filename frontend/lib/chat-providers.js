// Browser-only config + storage for the AI chatbot provider/model selection.
// Keys live here in localStorage and are sent to the server only per chat request.

import { scopedKey } from "./chat-scope.js";

// "exaone" is the self-hosted local LLM: it needs NO API key and is the default
// so the chatbot works out-of-the-box. Commercial providers stay opt-in (BYO key).
export const PROVIDERS = [
  { id: "exaone", label: "RAPID Local EXAONE (no key)", keyless: true },
  { id: "anthropic", label: "Claude" },
  { id: "openai", label: "OpenAI (Codex)" },
  { id: "gemini", label: "Gemini" },
];

export const DEFAULT_PROVIDER = "exaone";

const STORAGE_KEY = "rapid.chat.config.v1";

// True when the provider needs no API key (local EXAONE / "local" alias).
export function providerIsKeyless(id) {
  const found = PROVIDERS.find((p) => p.id === id);
  return Boolean(found && found.keyless) || id === "local";
}

function defaults() {
  return { provider: DEFAULT_PROVIDER, model: "", keys: {} };
}

export function loadChatConfig() {
  try {
    const raw = localStorage.getItem(scopedKey(STORAGE_KEY));
    if (!raw) return defaults();
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return defaults();
    const provider = typeof parsed.provider === "string" ? parsed.provider : DEFAULT_PROVIDER;
    let model = typeof parsed.model === "string" ? parsed.model : "";
    // Keyless providers (local EXAONE) serve a single model; drop any stale
    // model id carried over from a previously-selected commercial provider so
    // it is never sent to the local endpoint.
    if (providerIsKeyless(provider)) model = "";
    return {
      provider,
      model,
      keys: parsed.keys && typeof parsed.keys === "object" ? parsed.keys : {},
    };
  } catch (_e) {
    return defaults();
  }
}

export function saveChatConfig(cfg) {
  const safe = {
    provider: (cfg && cfg.provider) || DEFAULT_PROVIDER,
    model: (cfg && cfg.model) || "",
    keys: (cfg && cfg.keys && typeof cfg.keys === "object") ? cfg.keys : {},
  };
  try {
    localStorage.setItem(scopedKey(STORAGE_KEY), JSON.stringify(safe));
  } catch (_e) {
    /* storage full / disabled — non-fatal */
  }
  return safe;
}

export function chatConfigReady(cfg) {
  if (!cfg || typeof cfg !== "object") return false;
  // Keyless providers (EXAONE) are ready with just a provider — no key needed,
  // and the server supplies a default model when none is chosen.
  if (providerIsKeyless(cfg.provider)) return Boolean(cfg.provider);
  const key = cfg.keys && cfg.keys[cfg.provider];
  return Boolean(cfg.provider && key && cfg.model);
}

export function providerLabel(id) {
  const found = PROVIDERS.find((p) => p.id === id);
  return found ? found.label : String(id || "");
}
