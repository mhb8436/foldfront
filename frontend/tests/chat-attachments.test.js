import test from "node:test";
import assert from "node:assert/strict";

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const { getChatSessionId, attachmentChipLabel, withAttachments } =
  await import("../lib/chat-attachments.js");

test("getChatSessionId is stable across calls", () => {
  store.clear();
  const a = getChatSessionId();
  const b = getChatSessionId();
  assert.equal(a, b);
  assert.ok(a && a.length >= 8);
});

test("attachmentChipLabel shows name and KB size", () => {
  assert.equal(attachmentChipLabel({ name: "seq.fasta", size: 2048 }), "seq.fasta (2 KB)");
  assert.equal(attachmentChipLabel({ name: "x", size: 0 }), "x (0 KB)");
});

test("withAttachments injects attachments + session_id into a payload", () => {
  const payload = { provider: "openai", messages: [] };
  const out = withAttachments(payload, [{ name: "a", base64: "AA" }], "sess9");
  assert.deepEqual(out.attachments, [{ name: "a", base64: "AA" }]);
  assert.equal(out.session_id, "sess9");
  assert.equal(out.provider, "openai");
});
