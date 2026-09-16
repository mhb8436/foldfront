import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  buildProviderHealthPayload,
  buildProviderUpdatePayload,
  normalizeProviderType,
  providerConnectionSummary,
  visibleTokenLabel,
} from "../lib/model-providers.js";

test("provider type aliases normalize to the registry contract", () => {
  assert.equal(normalizeProviderType("http"), "http_api");
  assert.equal(normalizeProviderType("local_http"), "http_api");
  assert.equal(normalizeProviderType("runpod"), "runpod");
  assert.equal(normalizeProviderType("off"), "disabled");
  assert.equal(normalizeProviderType(""), "disabled");
});

test("provider update payload trims fields and never sends a blank token", () => {
  const payload = buildProviderUpdatePayload({
    modelKey: "proteinmpnn",
    scope: "user",
    providerType: " http ",
    endpointId: " ep-123 ",
    baseUrl: " http://127.0.0.1:18101/ ",
    token: "   ",
    timeoutS: "45",
    enabled: true,
  });

  assert.deepEqual(payload, {
    model_key: "proteinmpnn",
    scope: "user",
    provider: {
      provider_type: "http_api",
      endpoint_id: "ep-123",
      base_url: "http://127.0.0.1:18101",
      enabled: true,
      timeout_s: 45,
    },
  });
});

test("provider health payload uses unsaved form values", () => {
  const payload = buildProviderHealthPayload({
    modelKey: "alphafold2",
    providerType: "http_api",
    endpointId: "old-runpod-endpoint",
    baseUrl: " http://gpu.example:18161/ ",
    token: "",
    timeoutS: "30",
    enabled: true,
  });

  assert.deepEqual(payload, {
    model_key: "alphafold2",
    scope: "global",
    provider: {
      provider_type: "http_api",
      endpoint_id: "old-runpod-endpoint",
      base_url: "http://gpu.example:18161",
      enabled: true,
      timeout_s: 30,
    },
  });
});

test("provider token display prefers masked values and never exposes raw token text", () => {
  const provider = {
    token: "raw-secret-token",
    token_masked: "********oken",
  };

  assert.equal(visibleTokenLabel(provider), "********oken");
  assert.doesNotMatch(visibleTokenLabel(provider), /raw-secret-token/);
});

test("provider summary explains the active connection target", () => {
  assert.equal(
    providerConnectionSummary({ provider_type: "http_api", base_url: "http://gpu:18101" }),
    "HTTP API: http://gpu:18101"
  );
  assert.equal(
    providerConnectionSummary({ provider_type: "runpod", endpoint_id: "abc123" }),
    "RunPod: abc123"
  );
  assert.equal(providerConnectionSummary({ provider_type: "disabled" }), "Disabled");
});

test("frontend exposes an in-app model provider manager instead of the legacy RunPod admin link", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /id="runpodAdminBtn"[\s\S]*data-i18n="modelProviders\.open"/);
  assert.doesNotMatch(html, /runpod-admin/);
  assert.match(html, /id="modelProvidersPanel"/);
  assert.match(source, /pipeline\.model_provider_list/);
  assert.match(source, /pipeline\.model_provider_update/);
  assert.match(source, /pipeline\.model_provider_health/);
});

test("model provider UI supports inline health status and adding custom models", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /id="modelProviderAddBtn"/);
  assert.match(html, /id="modelProviderAddPanel"/);
  assert.match(html, /id="modelProviderSkillDownload"/);
  assert.match(html, /mimikyou0607\/proteinmpnn-runpod:latest/);
  assert.match(html, /modelProviders\.credential/);
  assert.match(html, /modelProviders\.guide\.title/);
  assert.match(source, /modelProviderCredentialLabel/);
  assert.match(source, /modelProviders\.runpodKey/);
  assert.match(source, /model_provider_skill\.zip/);
  assert.match(source, /downloadModelRegistrationSkill/);
  assert.match(source, /data-model-provider-action-status/);
  assert.match(source, /function saveCustomModelProvider/);
  assert.match(source, /custom:\s*true/);
  assert.match(source, /buildProviderHealthPayload\(/);
  assert.match(source, /pipeline\.model_provider_health",\s*payload/);
  assert.doesNotMatch(source, /modelProvidersStatus\.textContent = result\?\.ready/);
  assert.doesNotMatch(source, /modelProviderHealthBadge\(provider,\s*health/);
});

test("model provider UI uses provider type instead of a separate enabled checkbox", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(source, /data-model-provider-field="enabled"/);
  assert.doesNotMatch(source, /modelProviders\.enabled/);
  assert.doesNotMatch(source, /modelProviderFieldValue\(card,\s*"enabled"\)/);
});

test("topbar does not expose a separate account-console button", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(html, /id="accountBtn"/);
  assert.doesNotMatch(html, /data-i18n="action\.account"/);
  assert.doesNotMatch(source, /openAccountConsole/);
});

test("model provider API auth failures are handled as session expiry", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /function handleApiAuthFailure/);
  assert.match(source, /showLogin\(\)/);
  assert.match(source, /throw new Error\(t\("auth\.sessionExpired"\)\)/);
});

test("frontend admin panel exposes OIDC user approval controls", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /id="adminUserList"/);
  assert.match(html, /id="adminRefreshUsers"/);
  assert.match(source, /\/auth\/list_users/);
  assert.match(source, /\/auth\/update_user/);
  assert.match(source, /data-admin-user-status/);
  assert.match(source, /data-admin-user-role/);
});
