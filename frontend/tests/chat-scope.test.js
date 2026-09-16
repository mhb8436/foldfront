import test from "node:test";
import assert from "node:assert/strict";

const { setChatScope, scopedKey, chatScopeSuffix } = await import("../lib/chat-scope.js");

test("chatScopeSuffix prefers run_prefix, then derives from username, else default", () => {
  assert.equal(chatScopeSuffix({ run_prefix: "team_alice" }), "team_alice");
  // username-only derives a stable, sanitized suffix via buildUserPrefix.
  assert.equal(chatScopeSuffix({ username: "Bob Smith" }), "bob_smith");
  assert.equal(chatScopeSuffix(null), "default");
  assert.equal(chatScopeSuffix({}), "default");
});

test("scopedKey suffixes the base key for a real user", () => {
  setChatScope({ run_prefix: "alice" });
  assert.equal(scopedKey("rapid.chat.config.v1"), "rapid.chat.config.v1::alice");
});

test("scopedKey returns the bare key for anonymous / default", () => {
  setChatScope(null);
  assert.equal(scopedKey("rapid.chat.config.v1"), "rapid.chat.config.v1");
  setChatScope({}); // resolves to "default" -> bare key (anonymous stays stable)
  assert.equal(scopedKey("rapid.chat.config.v1"), "rapid.chat.config.v1");
});

test("switching scope changes the key deterministically", () => {
  setChatScope({ run_prefix: "one" });
  const k1 = scopedKey("k");
  setChatScope({ run_prefix: "two" });
  const k2 = scopedKey("k");
  assert.notEqual(k1, k2);
  assert.equal(k1, "k::one");
  assert.equal(k2, "k::two");
  setChatScope(null);
});
