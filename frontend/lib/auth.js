export function normalizeApiBase(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

export function shouldRestoreStoredSession({ token } = {}) {
  return Boolean(String(token || "").trim());
}

export function shouldAttemptSessionRestore({ token, oidcEnabled = false } = {}) {
  return shouldRestoreStoredSession({ token }) || Boolean(oidcEnabled);
}

export function shouldClearStoredSession({ status = 0, error = "" } = {}) {
  const normalizedStatus = Number.parseInt(status, 10);
  if ([401, 403].includes(normalizedStatus)) return true;
  const normalizedError = String(error || "")
    .trim()
    .toLowerCase();
  return ["unauthorized", "session invalid", "admin required"].includes(normalizedError);
}

export function resolveLoginMode({ authBootstrapPending = false, oidcConfig = null } = {}) {
  if (authBootstrapPending) return "loading";
  return oidcConfig && oidcConfig.enabled ? "oidc" : "local";
}

export function resolveDefaultApiBase({
  origin = typeof window !== "undefined" ? window.location.origin : "",
  pathname = typeof window !== "undefined" ? window.location.pathname : "",
} = {}) {
  if (origin && origin !== "null" && pathname.startsWith("/pipeline")) {
    return `${origin}/pipeline/api`;
  }
  if (origin && /localhost|127\.0\.0\.1/.test(origin)) {
    return "http://127.0.0.1:18080";
  }
  if (origin && origin !== "null") {
    return `${origin}/api`;
  }
  return "https://pipeline.k-biofoundrycopilot.duckdns.org/api";
}

export function buildOidcRedirectUri({
  origin = typeof window !== "undefined" ? window.location.origin : "",
  pathname = typeof window !== "undefined" ? window.location.pathname : "/",
} = {}) {
  const cleanPath = `/${String(pathname || "/")
    .trim()
    .replace(/^\/+/, "")
    .replace(/[#?].*$/, "")}`;
  if (origin && origin !== "null") {
    return `${origin}${cleanPath === "//" ? "/" : cleanPath}`;
  }
  return cleanPath === "//" ? "/" : cleanPath;
}

export function parseOidcCallback(search = typeof window !== "undefined" ? window.location.search : "") {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  return {
    code: String(params.get("code") || "").trim(),
    state: String(params.get("state") || "").trim(),
    error: String(params.get("error") || "").trim(),
    errorDescription: String(params.get("error_description") || "").trim(),
  };
}

export function stripOidcCallbackUrl(urlLike = typeof window !== "undefined" ? window.location.href : "/") {
  const url = new URL(String(urlLike || "/"), "http://localhost");
  url.searchParams.delete("code");
  url.searchParams.delete("state");
  url.searchParams.delete("session_state");
  url.searchParams.delete("iss");
  url.searchParams.delete("error");
  url.searchParams.delete("error_description");
  const search = url.searchParams.toString();
  return `${url.pathname}${search ? `?${search}` : ""}${url.hash}`;
}

export function buildOidcAuthorizationUrl({
  authorizationEndpoint,
  clientId,
  redirectUri,
  scopes = "openid profile email",
  state,
  codeChallenge,
} = {}) {
  if (!authorizationEndpoint || !clientId || !redirectUri || !state) {
    throw new Error("OIDC authorization parameters are incomplete.");
  }
  const url = new URL(String(authorizationEndpoint));
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", String(clientId));
  url.searchParams.set("redirect_uri", String(redirectUri));
  url.searchParams.set("scope", String(scopes || "openid profile email"));
  url.searchParams.set("state", String(state));
  if (codeChallenge) {
    url.searchParams.set("code_challenge", String(codeChallenge));
    url.searchParams.set("code_challenge_method", "S256");
  }
  return url.toString();
}

export function buildOidcLogoutUrl({
  endSessionEndpoint,
  postLogoutRedirectUri,
  clientId,
  idTokenHint,
} = {}) {
  if (!endSessionEndpoint) return "";
  const url = new URL(String(endSessionEndpoint));
  if (idTokenHint) {
    url.searchParams.set("id_token_hint", String(idTokenHint));
  }
  if (postLogoutRedirectUri) {
    url.searchParams.set("post_logout_redirect_uri", String(postLogoutRedirectUri));
  }
  if (clientId) {
    url.searchParams.set("client_id", String(clientId));
  }
  return url.toString();
}
