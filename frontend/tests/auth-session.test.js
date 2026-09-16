import test from "node:test";
import assert from "node:assert/strict";

import { shouldAttemptSessionRestore } from "../lib/auth.js";

test("shouldAttemptSessionRestore uses stored token when available", () => {
  assert.equal(shouldAttemptSessionRestore({ token: "abc.def", oidcEnabled: false }), true);
});

test("shouldAttemptSessionRestore probes OIDC cookie sessions without a stored token", () => {
  assert.equal(shouldAttemptSessionRestore({ token: "", oidcEnabled: true }), true);
});

test("shouldAttemptSessionRestore stays idle without token or OIDC mode", () => {
  assert.equal(shouldAttemptSessionRestore({ token: "", oidcEnabled: false }), false);
});
