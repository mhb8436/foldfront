import test from "node:test";
import assert from "node:assert/strict";

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const {
  loadConversations, upsertConversation, deleteConversation, conversationTitle, newConversationId,
} = await import("../lib/chat-conversations.js");
const { setChatScope } = await import("../lib/chat-scope.js");

test("empty by default", () => {
  store.clear();
  assert.deepEqual(loadConversations(), []);
});

test("conversationTitle uses first user message, capped 40", () => {
  assert.equal(conversationTitle([{ role: "ai", text: "hi" }, { role: "user", text: "hello world" }]), "hello world");
  assert.equal(conversationTitle([{ role: "user", text: "x".repeat(60) }]).length, 40);
  assert.equal(conversationTitle([]), "New chat");
});

test("upsert adds/updates and sorts newest-first", () => {
  store.clear();
  upsertConversation("a", [{ role: "user", text: "first" }], 100);
  upsertConversation("b", [{ role: "user", text: "second" }], 200);
  upsertConversation("a", [{ role: "user", text: "first again" }], 300); // update a
  const list = loadConversations();
  assert.equal(list[0].id, "a");
  assert.equal(list[0].title, "first again");
  assert.equal(list.length, 2);
});

test("delete removes by id", () => {
  store.clear();
  upsertConversation("a", [{ role: "user", text: "x" }], 1);
  upsertConversation("b", [{ role: "user", text: "y" }], 2);
  const after = deleteConversation("a");
  assert.deepEqual(after.map((c) => c.id), ["b"]);
});

test("newConversationId is unique-ish and stable string", () => {
  const a = newConversationId();
  assert.ok(typeof a === "string" && a.length >= 6);
});

test("caps at 30 newest", () => {
  store.clear();
  for (let i = 0; i < 35; i++) upsertConversation(`c${i}`, [{ role: "user", text: `m${i}` }], i);
  assert.equal(loadConversations().length, 30);
  assert.equal(loadConversations()[0].id, "c34");
});

test("per-user isolation: two accounts get disjoint conversation lists", () => {
  store.clear();
  setChatScope({ run_prefix: "alice" });
  upsertConversation("a", [{ role: "user", text: "alice secret" }], 1);
  assert.deepEqual(loadConversations().map((c) => c.id), ["a"]);

  // Switching users must NOT reveal the other account's chats.
  setChatScope({ run_prefix: "bob" });
  assert.deepEqual(loadConversations(), []);
  upsertConversation("b", [{ role: "user", text: "bob secret" }], 2);
  assert.deepEqual(loadConversations().map((c) => c.id), ["b"]);

  // Alice's data is intact and unchanged when we switch back.
  setChatScope({ run_prefix: "alice" });
  assert.deepEqual(loadConversations().map((c) => c.id), ["a"]);
  assert.equal(loadConversations()[0].title, "alice secret");

  // Distinct backing keys under the two scopes.
  assert.ok(store.has("rapid.chat.conversations.v1::alice"));
  assert.ok(store.has("rapid.chat.conversations.v1::bob"));

  setChatScope(null);
});

test("anonymous / no user falls back to the bare (unsuffixed) key", () => {
  store.clear();
  // Steady state (legacy one-time carry-over already done): a signed-in account
  // must NOT absorb a later anonymous user's bare-key chats.
  store.set("rapid.chat.legacyMigrated.v1", "1");
  setChatScope(null);
  upsertConversation("anon", [{ role: "user", text: "hi" }], 1);
  assert.ok(store.has("rapid.chat.conversations.v1"));

  // username-only user derives a stable suffix and is isolated from anonymous.
  setChatScope({ username: "carol" });
  assert.deepEqual(loadConversations(), []);

  setChatScope(null);
  assert.deepEqual(loadConversations().map((c) => c.id), ["anon"]);
});

test("one-time legacy carry-over: first signed-in account inherits global chats, others do not", () => {
  store.clear();
  // Pre-namespacing (shared) history under the bare key.
  store.set(
    "rapid.chat.conversations.v1",
    JSON.stringify([{ id: "old", title: "old", messages: [{ role: "user", text: "old" }], updatedAt: 1 }]),
  );
  setChatScope({ username: "alice", run_prefix: "alice" });
  assert.deepEqual(loadConversations().map((c) => c.id), ["old"]); // alice keeps her chats
  assert.equal(store.has("rapid.chat.conversations.v1"), false); // global removed
  assert.ok(store.has("rapid.chat.legacyMigrated.v1")); // one-time flag set

  setChatScope({ username: "bob", run_prefix: "bob" });
  assert.deepEqual(loadConversations(), []); // a different account does NOT inherit
  setChatScope(null);
});
