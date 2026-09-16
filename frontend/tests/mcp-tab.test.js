import test from "node:test";
import assert from "node:assert/strict";

test("pipeline MCP tab is Korean-first and includes VS Code plus Codex guidance", () => {
  return import("../lib/mcp-guide.js").then((mcpGuide) => {
    const html = mcpGuide.renderMcpGuideMarkup({
      lang: "ko",
      endpointUrl: "https://dev-pipeline.k-biofoundrycopilot.duckdns.org/mcp",
    });
    assert.equal(html.includes("https://dev-pipeline.k-biofoundrycopilot.duckdns.org/mcp"), true);
    assert.equal(html.includes("MCP: Open User Configuration"), true);
    assert.equal(html.includes("Codex"), true);
    assert.equal(html.includes("질문 예시"), true);
    assert.equal(html.includes('id="mcpTokenCopyBtn"'), true);
    assert.equal(html.includes('id="mcpSkillDownloadBtn"'), true);
    // Skill install guidance present (where to put the downloaded skill).
    assert.equal(html.includes("~/.claude/skills/"), true);
    // The placeholder token must be fully substituted, never shown raw.
    assert.equal(html.includes("__MCP_ENDPOINT__"), false);
  });
});

test("MCP tab: skill download + one master prompt that registers MCP (token placeholder + restart), advanced collapsed", () => {
  return import("../lib/mcp-guide.js").then((mcpGuide) => {
    const html = mcpGuide.renderMcpGuideMarkup({ lang: "ko", endpointUrl: "https://x.test/mcp" });
    // Primary flow: skill download + master prompt.
    assert.equal(html.includes('id="mcpSkillDownloadBtn"'), true);
    assert.equal(html.includes('id="mcpMasterPromptText"'), true);
    assert.equal(html.includes('id="mcpMasterPromptCopyBtn"'), true);
    // The master prompt instructs the AI to register the MCP server itself...
    assert.equal(html.includes("Authorization: Bearer"), true);
    assert.equal(html.includes("https://x.test/mcp"), true);
    // ...with a token placeholder that gets filled on copy (shown escaped in the <pre>).
    assert.equal(html.includes("KBF_SSO_ACCESS_TOKEN"), true);
    // ...and mentions restarting the client.
    assert.equal(html.includes("재시작"), true);
    // Detailed/manual setup is tucked into a collapsible <details> (incl. manual token copy).
    assert.equal(html.includes("<details"), true);
    assert.equal(html.includes('id="mcpTokenCopyBtn"'), true);
    assert.equal(html.includes("질문 예시"), true);
  });
});

test("Advanced section exposes the API-keys (PAT) card", () => {
  return import("../lib/mcp-guide.js").then((mcpGuide) => {
    const html = mcpGuide.renderMcpGuideMarkup({ lang: "ko", endpointUrl: "https://x.test/mcp" });
    assert.equal(html.includes('id="mcpKeyCreateBtn"'), true);
    assert.equal(html.includes('id="mcpKeysList"'), true);
    assert.equal(html.includes('id="mcpKeyTtl"'), true);
    assert.equal(html.includes("API key"), true);
  });
});

test("fillMasterPromptToken replaces the bearer-token placeholder", () => {
  return import("../lib/mcp-guide.js").then((mcpGuide) => {
    const out = mcpGuide.fillMasterPromptToken("...Authorization: Bearer <KBF_SSO_ACCESS_TOKEN> ...", "TKN.abc.123");
    assert.equal(out.includes("Bearer TKN.abc.123"), true);
    assert.equal(out.includes("<KBF_SSO_ACCESS_TOKEN>"), false);
  });
});

test("renderMcpGuideMarkup uses the given endpoint URL everywhere (no stale prod host)", () => {
  return import("../lib/mcp-guide.js").then((mcpGuide) => {
    const html = mcpGuide.renderMcpGuideMarkup({
      lang: "en",
      endpointUrl: "https://rapid-staging.kbiofoundry.kr/mcp",
    });
    assert.equal(html.includes("https://rapid-staging.kbiofoundry.kr/mcp"), true);
    // Must not leak the old hardcoded prod redirecting host when another env is given.
    assert.equal(html.includes("pipeline.k-biofoundrycopilot.duckdns.org/mcp"), false);
  });
});

test("resolveMcpEndpointUrl derives /mcp from origin and falls back when absent", () => {
  return import("../lib/mcp-guide.js").then((mcpGuide) => {
    assert.equal(
      mcpGuide.resolveMcpEndpointUrl({ origin: "https://dev-pipeline.k-biofoundrycopilot.duckdns.org" }),
      "https://dev-pipeline.k-biofoundrycopilot.duckdns.org/mcp"
    );
    // Trailing slash on origin is normalized.
    assert.equal(
      mcpGuide.resolveMcpEndpointUrl({ origin: "https://rapid.kbiofoundry.kr/" }),
      "https://rapid.kbiofoundry.kr/mcp"
    );
    // Empty/invalid origin falls back to the canonical prod endpoint.
    assert.equal(mcpGuide.resolveMcpEndpointUrl({ origin: "" }), "https://rapid.kbiofoundry.kr/mcp");
    assert.equal(mcpGuide.resolveMcpEndpointUrl({ origin: "null" }), "https://rapid.kbiofoundry.kr/mcp");
  });
});

test("buildMcpJsonSnippetWithToken injects the real token and endpoint", () => {
  return import("../lib/mcp-guide.js").then((mcpGuide) => {
    const snippet = mcpGuide.buildMcpJsonSnippetWithToken(
      "ABC.TOKEN.123",
      "https://dev-pipeline.k-biofoundrycopilot.duckdns.org/mcp"
    );
    assert.equal(snippet.includes("ABC.TOKEN.123"), true);
    assert.equal(snippet.includes("<KBF_SSO_ACCESS_TOKEN>"), false);
    assert.equal(snippet.includes("protein-pipeline"), true);
    assert.equal(snippet.includes("https://dev-pipeline.k-biofoundrycopilot.duckdns.org/mcp"), true);
  });
});
