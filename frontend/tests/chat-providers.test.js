import test from "node:test";
import assert from "node:assert/strict";

// Minimal localStorage stub (module reads it at call time, not import time).
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const {
  PROVIDERS,
  DEFAULT_PROVIDER,
  providerIsKeyless,
  loadChatConfig,
  saveChatConfig,
  chatConfigReady,
  providerLabel,
} = await import("../lib/chat-providers.js");
const { setChatScope } = await import("../lib/chat-scope.js");

test("PROVIDERS lists exaone (default, keyless) plus the three commercial providers", () => {
  assert.deepEqual(PROVIDERS.map((p) => p.id), ["exaone", "anthropic", "openai", "gemini"]);
  assert.equal(DEFAULT_PROVIDER, "exaone");
  const exaone = PROVIDERS.find((p) => p.id === "exaone");
  assert.equal(exaone.keyless, true);
  assert.match(exaone.label, /EXAONE/);
});

test("providerIsKeyless is true for exaone/local and false for commercial", () => {
  assert.equal(providerIsKeyless("exaone"), true);
  assert.equal(providerIsKeyless("local"), true);
  assert.equal(providerIsKeyless("anthropic"), false);
  assert.equal(providerIsKeyless("openai"), false);
});

test("loadChatConfig defaults to exaone on empty storage", () => {
  store.clear();
  const cfg = loadChatConfig();
  assert.equal(cfg.provider, "exaone");
  assert.equal(cfg.model, "");
  assert.deepEqual(cfg.keys, {});
});

test("loadChatConfig tolerates corrupt storage", () => {
  store.clear();
  store.set("rapid.chat.config.v1", "{not json");
  const cfg = loadChatConfig();
  assert.equal(cfg.provider, "exaone");
});

test("saveChatConfig round-trips", () => {
  store.clear();
  saveChatConfig({ provider: "openai", model: "gpt-4o", keys: { openai: "sk" } });
  const cfg = loadChatConfig();
  assert.equal(cfg.provider, "openai");
  assert.equal(cfg.model, "gpt-4o");
  assert.equal(cfg.keys.openai, "sk");
});

test("chatConfigReady requires provider, its key, and a model for commercial providers", () => {
  assert.equal(chatConfigReady({ provider: "openai", model: "", keys: { openai: "sk" } }), false);
  assert.equal(chatConfigReady({ provider: "openai", model: "gpt-4o", keys: {} }), false);
  assert.equal(chatConfigReady({ provider: "openai", model: "gpt-4o", keys: { openai: "sk" } }), true);
});

test("chatConfigReady is true for exaone with NO key and NO model", () => {
  // EXAONE works out-of-the-box: keyless, server supplies the default model.
  assert.equal(chatConfigReady({ provider: "exaone", model: "", keys: {} }), true);
  assert.equal(chatConfigReady({ provider: "exaone" }), true);
  assert.equal(chatConfigReady({ provider: "local", keys: {} }), true);
});

test("providerLabel maps id to label and falls back to id", () => {
  assert.equal(providerLabel("anthropic"), "Claude");
  assert.equal(providerLabel("exaone"), "RAPID Local EXAONE (no key)");
  assert.equal(providerLabel("mystery"), "mystery");
});

test("per-user isolation: API keys/config do not leak across accounts", () => {
  store.clear();
  setChatScope({ run_prefix: "alice" });
  saveChatConfig({ provider: "openai", model: "gpt-4o", keys: { openai: "sk-alice" } });

  // A different account starts from clean defaults — never sees alice's key.
  setChatScope({ run_prefix: "bob" });
  const bob = loadChatConfig();
  assert.equal(bob.provider, "exaone");
  assert.deepEqual(bob.keys, {});
  saveChatConfig({ provider: "anthropic", model: "claude", keys: { anthropic: "sk-bob" } });

  // Switching back restores alice's own config intact.
  setChatScope({ run_prefix: "alice" });
  const alice = loadChatConfig();
  assert.equal(alice.provider, "openai");
  assert.equal(alice.keys.openai, "sk-alice");

  // Distinct backing keys per scope; anonymous uses the bare key.
  assert.ok(store.has("rapid.chat.config.v1::alice"));
  assert.ok(store.has("rapid.chat.config.v1::bob"));
  assert.equal(store.has("rapid.chat.config.v1"), false);

  setChatScope(null);
});
