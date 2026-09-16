import test from "node:test";
import assert from "node:assert/strict";

import {
  buildOidcAuthorizationUrl,
  buildOidcLogoutUrl,
  buildOidcRedirectUri,
  parseOidcCallback,
  stripOidcCallbackUrl,
  resolveDefaultApiBase,
  shouldClearStoredSession,
  shouldRestoreStoredSession,
} from "../lib/auth.js";

test("resolveDefaultApiBase prefers subdomain api host", () => {
  assert.equal(
    resolveDefaultApiBase({
      origin: "https://pipeline.k-biofoundrycopilot.duckdns.org",
      pathname: "/",
    }),
    "https://pipeline.k-biofoundrycopilot.duckdns.org/api"
  );
});

test("resolveDefaultApiBase preserves legacy /pipeline path routing", () => {
  assert.equal(
    resolveDefaultApiBase({
      origin: "https://k-biofoundrycopilot.duckdns.org",
      pathname: "/pipeline/",
    }),
    "https://k-biofoundrycopilot.duckdns.org/pipeline/api"
  );
});

test("buildOidcRedirectUri strips query strings", () => {
  assert.equal(
    buildOidcRedirectUri({
      origin: "https://pipeline.k-biofoundrycopilot.duckdns.org",
      pathname: "/index.html?foo=bar",
    }),
    "https://pipeline.k-biofoundrycopilot.duckdns.org/index.html"
  );
});

test("parseOidcCallback extracts code and provider errors", () => {
  assert.deepEqual(parseOidcCallback("?code=abc123&state=xyz"), {
    code: "abc123",
    state: "xyz",
    error: "",
    errorDescription: "",
  });
  assert.deepEqual(parseOidcCallback("?error=access_denied&error_description=cancelled"), {
    code: "",
    state: "",
    error: "access_denied",
    errorDescription: "cancelled",
  });
});

test("stripOidcCallbackUrl removes callback-only parameters", () => {
  assert.equal(
    stripOidcCallbackUrl(
      "https://pipeline.k-biofoundrycopilot.duckdns.org/?code=abc123&state=xyz&session_state=s1&iss=https%3A%2F%2Fsso.example%2Frealms%2Fkbf#frag"
    ),
    "/#frag"
  );
  assert.equal(
    stripOidcCallbackUrl(
      "https://pipeline.k-biofoundrycopilot.duckdns.org/?view=monitor&session_state=s1&iss=https%3A%2F%2Fsso.example%2Frealms%2Fkbf"
    ),
    "/?view=monitor"
  );
});

test("buildOidcAuthorizationUrl includes PKCE and redirect fields", () => {
  const url = new URL(
    buildOidcAuthorizationUrl({
      authorizationEndpoint: "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/protocol/openid-connect/auth",
      clientId: "protein-pipeline",
      redirectUri: "https://pipeline.k-biofoundrycopilot.duckdns.org/",
      scopes: "openid profile email",
      state: "state-123",
      codeChallenge: "challenge-xyz",
    })
  );

  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("client_id"), "protein-pipeline");
  assert.equal(url.searchParams.get("redirect_uri"), "https://pipeline.k-biofoundrycopilot.duckdns.org/");
  assert.equal(url.searchParams.get("scope"), "openid profile email");
  assert.equal(url.searchParams.get("state"), "state-123");
  assert.equal(url.searchParams.get("code_challenge"), "challenge-xyz");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
});

test("buildOidcLogoutUrl includes redirect and id token hint", () => {
  const url = new URL(
    buildOidcLogoutUrl({
      endSessionEndpoint: "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/protocol/openid-connect/logout",
      postLogoutRedirectUri: "https://pipeline.k-biofoundrycopilot.duckdns.org/",
      clientId: "protein-pipeline",
      idTokenHint: "header.payload.sig",
    })
  );

  assert.equal(
    url.searchParams.get("post_logout_redirect_uri"),
    "https://pipeline.k-biofoundrycopilot.duckdns.org/"
  );
  assert.equal(url.searchParams.get("client_id"), "protein-pipeline");
  assert.equal(url.searchParams.get("id_token_hint"), "header.payload.sig");
});

test("shouldRestoreStoredSession only depends on a persisted token", () => {
  assert.equal(shouldRestoreStoredSession({ token: "abc.def" }), true);
  assert.equal(shouldRestoreStoredSession({ token: "   " }), false);
  assert.equal(shouldRestoreStoredSession({ token: "", user: { username: "tester" } }), false);
});

test("shouldClearStoredSession only clears for explicit auth failures", () => {
  assert.equal(shouldClearStoredSession({ status: 401, error: "unauthorized" }), true);
  assert.equal(shouldClearStoredSession({ status: 403, error: "admin required" }), true);
  assert.equal(shouldClearStoredSession({ status: 502, error: "bad gateway" }), false);
  assert.equal(shouldClearStoredSession({ error: "unauthorized" }), true);
  assert.equal(shouldClearStoredSession({ error: "network error" }), false);
});
