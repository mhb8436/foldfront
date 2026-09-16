import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { resolveLoginMode } from "../lib/auth.js";

test("resolveLoginMode stays in loading until auth bootstrap completes", () => {
  assert.equal(resolveLoginMode({ authBootstrapPending: true, oidcConfig: null }), "loading");
  assert.equal(resolveLoginMode({ authBootstrapPending: true, oidcConfig: { enabled: true } }), "loading");
});

test("resolveLoginMode switches to oidc or local after bootstrap", () => {
  assert.equal(resolveLoginMode({ authBootstrapPending: false, oidcConfig: { enabled: true } }), "oidc");
  assert.equal(resolveLoginMode({ authBootstrapPending: false, oidcConfig: { enabled: false } }), "local");
  assert.equal(resolveLoginMode({ authBootstrapPending: false, oidcConfig: null }), "local");
});

test("login shell keeps auth actions hidden until bootstrap resolves the mode", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

  assert.match(html, /id="loginLocalDesc" class="hidden"/);
  assert.match(html, /id="loginLocalForm" class="login-stack hidden"/);
  assert.match(html, /id="loginSsoDesc" class="hidden"/);
  assert.match(html, /id="loginSsoActions" class="login-stack hidden"/);
  assert.match(html, /id="loginLoading" class="hint"/);
});
