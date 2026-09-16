const PROVIDER_TYPE_ALIASES = Object.freeze({
  http: "http_api",
  local_http: "http_api",
  local: "http_api",
  api: "http_api",
  http_api: "http_api",
  runpod: "runpod",
  serverless: "runpod",
  disabled: "disabled",
  disable: "disabled",
  off: "disabled",
  none: "disabled",
});

function trimText(value) {
  return String(value ?? "").trim();
}

export function normalizeProviderType(value) {
  const raw = trimText(value).toLowerCase();
  return PROVIDER_TYPE_ALIASES[raw] || "disabled";
}

export function normalizeProviderScope(value) {
  const raw = trimText(value).toLowerCase().replace("-", "_");
  if (["user", "personal", "mine"].includes(raw)) return "user";
  return "global";
}

export function normalizeProviderBaseUrl(value) {
  const raw = trimText(value);
  return raw.replace(/\/+$/, "");
}

export function visibleTokenLabel(provider) {
  const masked = trimText(provider?.token_masked);
  if (masked) return masked;
  return trimText(provider?.token) ? "********" : "";
}

export function providerConnectionSummary(provider) {
  const type = normalizeProviderType(provider?.provider_type);
  if (type === "http_api") {
    const baseUrl = normalizeProviderBaseUrl(provider?.base_url);
    return baseUrl ? `HTTP API: ${baseUrl}` : "HTTP API: not configured";
  }
  if (type === "runpod") {
    const endpointId = trimText(provider?.endpoint_id);
    return endpointId ? `RunPod: ${endpointId}` : "RunPod: not configured";
  }
  return "Disabled";
}

export function providerConfigured(provider) {
  const type = normalizeProviderType(provider?.provider_type);
  if (!provider?.enabled || type === "disabled") return false;
  if (type === "http_api") return Boolean(normalizeProviderBaseUrl(provider?.base_url));
  if (type === "runpod") return Boolean(trimText(provider?.endpoint_id));
  return false;
}

export function buildProviderUpdatePayload({
  modelKey,
  scope,
  providerType,
  endpointId,
  baseUrl,
  token,
  timeoutS,
}) {
  const normalizedType = normalizeProviderType(providerType);
  const provider = {
    provider_type: normalizedType,
    endpoint_id: trimText(endpointId),
    base_url: normalizeProviderBaseUrl(baseUrl),
    enabled: normalizedType !== "disabled",
  };
  const tokenText = trimText(token);
  if (tokenText) {
    provider.token = tokenText;
  }
  const parsedTimeout = Number.parseInt(trimText(timeoutS), 10);
  if (Number.isFinite(parsedTimeout) && parsedTimeout > 0) {
    provider.timeout_s = parsedTimeout;
  }
  return {
    model_key: trimText(modelKey),
    scope: normalizeProviderScope(scope),
    provider,
  };
}

export function buildProviderHealthPayload(fields) {
  return buildProviderUpdatePayload(fields);
}

export function sortModelProviders(providers) {
  return [...(Array.isArray(providers) ? providers : [])].sort((left, right) => {
    const leftOrder = Number.isFinite(left?.order) ? Number(left.order) : 999;
    const rightOrder = Number.isFinite(right?.order) ? Number(right.order) : 999;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return trimText(left?.label || left?.model_key).localeCompare(trimText(right?.label || right?.model_key));
  });
}
