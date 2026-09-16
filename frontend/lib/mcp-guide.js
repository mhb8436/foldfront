const MCP_DEFAULT_ENDPOINT_URL = "https://rapid.kbiofoundry.kr/mcp";
const MCP_ENDPOINT_TOKEN = "__MCP_ENDPOINT__";
const MCP_SERVER_NAME = "protein-pipeline";

export function resolveMcpEndpointUrl({
  origin = typeof window !== "undefined" ? window.location.origin : "",
} = {}) {
  const normalized = String(origin || "").trim().replace(/\/$/, "");
  if (normalized && normalized !== "null") {
    return `${normalized}/mcp`;
  }
  return MCP_DEFAULT_ENDPOINT_URL;
}
const TOKEN_PLACEHOLDER = "<KBF_SSO_ACCESS_TOKEN>";
const TOKEN_PLACEHOLDER_HTML = "&lt;KBF_SSO_ACCESS_TOKEN&gt;";

const GUIDE_COPY = {
  en: {
    title: "Connect your AI to the pipeline",
    description: "Three steps: give your AI client the connection, the skill, and one prompt — then it does the rest, from MCP connection to analysis.",
    flow: {
      step1Title: "1) Download the skill",
      step1Desc: "Download the protein-pipeline-stepper skill and save the zip (e.g. to your Downloads). Your AI will install it in the next step.",
      step2Title: "2) Paste one prompt — the AI sets up the rest",
      step2Desc: "Copy this master prompt — a long-lived API key is generated and filled in automatically (no token refresh). Paste it to your AI: it installs the skill, registers the protein-pipeline MCP server, asks you to restart the client if needed, then runs your analysis.",
      masterNote:
        "Copying generates a long-lived API key (default 90 days) and embeds it — so setup and long jobs never hit a token-expiry. Revoke it anytime under Advanced › API keys.",
      masterPrompt:
        `You are my coding agent with shell access. Set up and use the protein-pipeline pipeline:\n\n1. Install the skill: I downloaded \`protein-pipeline-stepper.zip\` (check my Downloads). Unzip it and put the \`protein-pipeline-stepper/\` folder in your skills directory (e.g. ~/.claude/skills/).\n2. Register an MCP server named \`protein-pipeline\` using your client's mechanism (VS Code mcp.json, \`codex mcp add\`, etc.):\n   - type: HTTP (streamable)\n   - URL: ${MCP_ENDPOINT_TOKEN}\n   - Header: Authorization: Bearer ${TOKEN_PLACEHOLDER}\n3. If the new skill or MCP server is not picked up, tell me to restart/reload the client, then continue once I confirm.\n4. Then, using the protein-pipeline-stepper skill, do this analysis:\n\n«describe your analysis task, e.g. run standalone ColabFold on this FASTA: ...»\n\nFirst ask me which mode: (1) full pipeline run (default), (2) Studio — stop after each stage for me to review and decide, or (3) a single model standalone (RFD3 or ColabFold/AF2 are the common ones; MSA/ProteinMPNN/SoluProt/DiffDock/BioEmu also available). For a full or Studio run, also ask (one message) defaults-vs-advanced and whether to enable surrogate triage, then post a checkpoint after each stage (key results, not just paths) — in Studio mode wait for my decision (move forward / rerun / stop) before continuing. Follow the skill: reuse one stable run_id, check pipeline.status before running, poll long-running jobs instead of re-running, pass file contents (not paths), and if a result looks wrong or fails, diagnose and ask me before re-running.`,
      masterPromptButton: "Copy master prompt (with my token)",
      advancedSummary: "Advanced / manual setup (API keys, copy mcp.json with token, endpoint URL, VS Code / Codex steps, verify, prompt examples)",
    },
    keys: {
      title: "API keys (no token refresh)",
      desc: "Create a long-lived API key to use instead of the short-lived sign-in token — ideal for long jobs and clients like Codex. Use it as the Bearer token in your mcp.json. Shown once; revoke anytime.",
      labelPlaceholder: "Label (e.g. my-laptop)",
      createBtn: "Create API key",
      never: "No expiry",
      newPrefix: "New key (copied — shown once):",
      empty: "No API keys yet.",
      revoke: "Revoke",
      note: "Keep API keys secret — anyone with the key can act as you (within your run scope). Revoke unused keys.",
    },
    endpoint: {
      title: "1) Remote MCP endpoint",
      description: "Public HTTP MCP endpoint for the pipeline service.",
      items: [
        `<strong>URL</strong>: <code>${MCP_ENDPOINT_TOKEN}</code>`,
        `<strong>Header</strong>: <code>Authorization: Bearer ${TOKEN_PLACEHOLDER_HTML}</code>`,
        "Non-admin users only see allowed tools, and run-scoped tools only work for their own <code>run_id</code> values.",
      ],
    },
    config: {
      title: "2) VS Code mcp.json",
      description:
        "Open <strong>MCP: Open User Configuration</strong> and paste this into <code>mcp.json</code>.",
    },
    token: {
      title: "3) How to get the token",
      description: "Choose the token source that matches your auth mode.",
      copyButton: "Copy mcp.json with my token",
      downloadButton: "Download skill",
      autoNote:
        "One click fills your bearer token into the mcp.json above and copies it. The token is a short-lived sign-in token — if MCP calls start failing, click again to refresh it.",
      installNote:
        "To use the downloaded skill: unzip it and put the <code>protein-pipeline-stepper/</code> folder in your agent's skills directory (Claude Code: <code>~/.claude/skills/</code>), then reload/restart the client so it discovers the skill.",
      items: [
        "<strong>Local auth mode</strong>: open browser devtools, go to <code>Local Storage</code>, and copy <code>kbf.token</code>.",
        `<strong>OIDC / KBF SSO mode</strong>: sign in to a KBF SSO-backed service, open the notebook service MCP page, then open browser devtools and inspect <code>Local Storage</code> &gt; <code>auth-storage</code>. Copy the <code>access_token</code> value and reuse it as <code>${TOKEN_PLACEHOLDER_HTML}</code> in <code>mcp.json</code>.`,
      ],
      note: "If you are signed in with SSO and <code>kbf.token</code> is empty in this app, that is expected. The pipeline UI currently uses the server-side session cookie for the browser session, while VS Code MCP still requires a bearer token in <code>mcp.json</code>.",
    },
    codex: {
      title: "4) Use it from Codex",
      description: "Codex can use the same endpoint and Authorization header as VS Code.",
      steps: [
        `Add an MCP server named <code>${MCP_SERVER_NAME}</code> in your Codex client.`,
        `Set the server URL to <code>${MCP_ENDPOINT_TOKEN}</code>.`,
        `Store <code>Authorization: Bearer ${TOKEN_PLACEHOLDER_HTML}</code> with the server configuration.`,
        "Explicitly mention MCP in your prompt so the tool call is unambiguous.",
      ],
    },
    verify: {
      title: "5) Verify it works",
      description: "After saving the configuration:",
      steps: [
        `Run <strong>MCP: List Servers</strong> and confirm <code>${MCP_SERVER_NAME}</code> is available.`,
        "Ask VS Code Copilot or Codex to call MCP for a run status or a pipeline launch.",
        "Check that returned <code>run_id</code> values use your user-scoped prefix.",
      ],
    },
    prompts: {
      title: "6) Prompt examples",
      description: "Start with direct MCP wording so the tool is clearly invoked.",
      examples: [
        "Use MCP to list my recent protein pipeline runs.",
        "Use MCP to check the status of run_id abc_20260316_123456_deadbeef.",
        "Use MCP to build a pipeline.run example for the current signed-in user.",
        "Use MCP to run AlphaFold2 standalone (pipeline.af2_predict) on this FASTA sequence.",
        "Use MCP to run a single design stage only: pipeline.run with stop_after=design.",
        "Codex, use MCP to list my recent protein pipeline runs.",
      ],
    },
  },
  ko: {
    title: "AI를 파이프라인에 연결",
    description: "두 단계면 됩니다 — skill을 받고, 토큰이 담긴 마스터 프롬프트 하나만 AI에 붙여넣으면 skill 등록·MCP 서버 등록·분석까지 AI가 알아서 합니다.",
    flow: {
      step1Title: "1) 스킬 다운로드",
      step1Desc: "protein-pipeline-stepper 스킬 zip을 받아 저장하세요(예: 다운로드 폴더). 설치는 다음 단계에서 AI가 합니다.",
      step2Title: "2) 프롬프트 하나만 붙여넣기 — 나머지는 AI가",
      step2Desc: "이 마스터 프롬프트를 복사하면 장수명 API key가 자동 생성되어 채워집니다(토큰 갱신 불필요). AI에 붙여넣으면 스킬을 설치하고 protein-pipeline MCP 서버를 등록하며, 필요하면 재시작을 요청한 뒤 분석을 수행합니다.",
      masterNote:
        "복사하면 장수명 API key(기본 90일)가 생성되어 박힙니다 — 그래서 설정·장시간 작업 중 토큰 만료가 없습니다. 필요하면 고급 › API keys에서 언제든 취소하세요.",
      masterPrompt:
        `너는 셸 접근 권한이 있는 내 코딩 에이전트야. protein-pipeline 파이프라인을 설정하고 사용해:\n\n1. 스킬 설치: 내가 \`protein-pipeline-stepper.zip\`을 다운로드했어(다운로드 폴더 확인). 압축을 풀어 \`protein-pipeline-stepper/\` 폴더를 네 skills 디렉터리(예: ~/.claude/skills/)에 넣어.\n2. \`protein-pipeline\` 이름으로 MCP 서버를 네 클라이언트 방식(VS Code mcp.json, \`codex mcp add\` 등)으로 등록해:\n   - 종류: HTTP (streamable)\n   - URL: ${MCP_ENDPOINT_TOKEN}\n   - 헤더: Authorization: Bearer ${TOKEN_PLACEHOLDER}\n3. 새 스킬이나 MCP 서버가 인식되지 않으면, 나에게 클라이언트를 재시작/새로고침하라고 말하고, 내가 확인하면 계속해.\n4. 그런 다음 protein-pipeline-stepper 스킬로 다음 분석을 해줘:\n\n《분석 작업을 설명, 예: 이 FASTA로 ColabFold 단독 실행: ...》\n\n먼저 어떤 모드인지 물어봐: (1) 전체 파이프라인 실행(기본), (2) Studio — 각 단계 후 멈춰 내가 검토·결정, (3) 단독 모델 실행(RFD3나 ColabFold/AF2가 흔함; MSA/ProteinMPNN/SoluProt/DiffDock/BioEmu도 가능). 전체나 Studio면 기본 vs 고급, surrogate triage 여부도 한 메시지로 묻고, 각 단계 후 핵심 결과(경로뿐 아니라 내용)로 체크포인트를 남겨 — Studio 모드면 다음 진행 전에 내 결정(다음/재실행/멈춤)을 기다려. 스킬 규칙을 지켜: run_id 하나 재사용, 실행 전 pipeline.status 확인, 오래 걸리는 작업은 재실행 말고 폴링, 파일은 경로 대신 내용 전달, 결과가 이상하거나 실패하면 진단 후 재실행 전 나에게 확인.`,
      masterPromptButton: "마스터 프롬프트 복사 (내 토큰 포함)",
      advancedSummary: "고급 / 수동 설정 (API key · 토큰 mcp.json 복사 · 엔드포인트 URL · VS Code / Codex 단계 · 검증 · 프롬프트 예시)",
    },
    keys: {
      title: "API key (토큰 갱신 불필요)",
      desc: "짧은 로그인 토큰 대신 쓸 수 있는 장수명 API key를 만듭니다 — 장시간 작업이나 Codex 같은 클라이언트에 적합합니다. mcp.json의 Bearer 토큰 자리에 넣으세요. 한 번만 표시되며 언제든 취소할 수 있습니다.",
      labelPlaceholder: "라벨 (예: my-laptop)",
      createBtn: "API key 발급",
      never: "만료 없음",
      newPrefix: "새 key (복사됨 — 한 번만 표시):",
      empty: "아직 API key가 없습니다.",
      revoke: "취소",
      note: "API key는 비밀로 보관하세요 — key를 가진 사람은 당신 권한(run 범위 내)으로 행동할 수 있습니다. 안 쓰는 key는 취소하세요.",
    },
    endpoint: {
      title: "1) 원격 MCP 엔드포인트",
      description: "파이프라인 서비스용 공용 HTTP MCP endpoint입니다.",
      items: [
        `<strong>URL</strong>: <code>${MCP_ENDPOINT_TOKEN}</code>`,
        `<strong>헤더</strong>: <code>Authorization: Bearer ${TOKEN_PLACEHOLDER_HTML}</code>`,
        "일반 사용자는 허용된 도구만 볼 수 있고, run 범위 도구는 자신의 <code>run_id</code>에 대해서만 동작합니다.",
      ],
    },
    config: {
      title: "2) VS Code 설정",
      description: "<strong>MCP: Open User Configuration</strong>을 열고 아래 내용을 <code>mcp.json</code>에 붙여 넣으세요.",
    },
    token: {
      title: "3) 토큰 가져오기",
      description: "현재 인증 방식에 맞는 토큰 원본을 선택하세요.",
      copyButton: "내 토큰으로 mcp.json 복사",
      downloadButton: "skill 다운로드",
      autoNote:
        "버튼 한 번이면 위 mcp.json에 bearer 토큰을 채워 클립보드에 복사합니다. 이 토큰은 수명이 짧은 로그인 토큰이라, MCP 호출이 실패하기 시작하면 다시 눌러 갱신하세요.",
      installNote:
        "다운로드한 skill 사용법: 압축을 풀어 <code>protein-pipeline-stepper/</code> 폴더를 에이전트의 skills 디렉터리(Claude Code: <code>~/.claude/skills/</code>)에 넣고, 클라이언트를 새로고침/재시작하면 인식됩니다.",
      items: [
        "<strong>로컬 인증 모드</strong>: 브라우저 개발자 도구를 열고 <code>Local Storage</code>에서 <code>kbf.token</code> 값을 복사하세요.",
        `<strong>OIDC / KBF SSO 모드</strong>: KBF SSO가 적용된 서비스에 로그인한 뒤 notebook service MCP 페이지를 열고, 브라우저 개발자 도구에서 <code>Local Storage</code> &gt; <code>auth-storage</code>를 확인하세요. 그 안의 <code>access_token</code> 값을 복사해 VS Code 또는 Codex 설정의 <code>${TOKEN_PLACEHOLDER_HTML}</code> 자리에 넣으면 됩니다.`,
      ],
      note: "SSO로 로그인한 상태에서 이 앱의 <code>kbf.token</code>이 비어 있어도 정상입니다. 현재 pipeline UI는 브라우저 세션에서 서버측 세션 쿠키를 사용하고, VS Code와 Codex의 MCP 연결은 별도 bearer token을 저장해야 합니다.",
    },
    codex: {
      title: "4) Codex에서 사용",
      description: "Codex도 VS Code와 같은 endpoint와 Authorization 헤더를 사용하면 됩니다.",
      steps: [
        `<code>${MCP_SERVER_NAME}</code> 라는 이름으로 MCP 서버를 추가합니다.`,
        `URL은 <code>${MCP_ENDPOINT_TOKEN}</code> 를 사용합니다.`,
        `<code>Authorization: Bearer ${TOKEN_PLACEHOLDER_HTML}</code> 헤더를 함께 저장합니다.`,
        "질문할 때는 MCP 사용을 직접 적어 도구 호출이 분명하게 보이게 합니다.",
      ],
    },
    verify: {
      title: "5) 연결 확인",
      description: "설정을 저장한 뒤 아래를 확인하세요.",
      steps: [
        `먼저 <strong>MCP: List Servers</strong>를 실행해 <code>${MCP_SERVER_NAME}</code>가 보이는지 확인합니다.`,
        "VS Code나 Codex에 MCP로 run 상태 조회나 pipeline 실행을 요청합니다.",
        "반환된 <code>run_id</code>가 현재 사용자 prefix를 사용하는지 확인합니다.",
      ],
    },
    prompts: {
      title: "6) 질문 예시",
      description: "도구 호출이 분명하게 보이도록 MCP를 직접 언급하는 문장으로 시작하세요.",
      examples: [
        "MCP를 이용해서 내 protein pipeline 최근 run 목록을 보여줘.",
        "MCP를 이용해서 run_id abc_20260316_123456_deadbeef 상태를 확인해줘.",
        "MCP를 이용해서 현재 사용자 기준으로 실행 가능한 pipeline.run 예시를 만들어줘.",
        "MCP를 이용해서 이 FASTA로 AlphaFold2 단독 실행(pipeline.af2_predict)을 해줘.",
        "MCP를 이용해서 design 단계만 단독 실행해줘: pipeline.run에 stop_after=design.",
        "Codex에서 MCP를 사용해서 내 최근 pipeline run들을 보여줘.",
      ],
    },
  },
};

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderList(items = []) {
  return `<div class="mcp-guide-list">${items.map((item) => `<div>${item}</div>`).join("")}</div>`;
}

function renderSteps(items = []) {
  return `<ol class="mcp-guide-steps">${items.map((item) => `<li>${item}</li>`).join("")}</ol>`;
}

function renderCodeBlock(text) {
  return `<pre class="mcp-guide-code"><code>${escapeHtml(text)}</code></pre>`;
}

function buildMcpJsonSnippet(endpointUrl = resolveMcpEndpointUrl()) {
  return JSON.stringify(
    {
      servers: {
        [MCP_SERVER_NAME]: {
          type: "http",
          url: endpointUrl,
          headers: {
            Authorization: `Bearer ${TOKEN_PLACEHOLDER}`,
          },
        },
      },
    },
    null,
    2
  );
}

export function buildMcpJsonSnippetWithToken(token, endpointUrl = resolveMcpEndpointUrl()) {
  const safe = String(token == null ? "" : token);
  return buildMcpJsonSnippet(endpointUrl).replaceAll(TOKEN_PLACEHOLDER, safe);
}

export function fillMasterPromptToken(text, token) {
  const safe = String(token == null ? "" : token);
  return String(text || "").replaceAll(TOKEN_PLACEHOLDER, safe);
}

function buildPromptSnippet(examples = []) {
  return examples.join("\n\n");
}

function guideCopyFor(lang = "en") {
  return GUIDE_COPY[lang] || GUIDE_COPY.en;
}

export function renderMcpGuideMarkup({ lang = "en", endpointUrl = resolveMcpEndpointUrl() } = {}) {
  const copy = guideCopyFor(lang);
  const markup = `
    <div class="panel-header">
      <h3>${copy.title}</h3>
      <p>${copy.description}</p>
    </div>

    <div class="tab-grid monitor-grid mcp-guide-grid">
      <div class="status-card mcp-guide-card">
        <div class="panel-header small">
          <h3>${copy.flow.step1Title}</h3>
          <p>${copy.flow.step1Desc}</p>
        </div>
        <div class="mcp-guide-actions">
          <button type="button" id="mcpSkillDownloadBtn" class="btn-secondary">${copy.token.downloadButton}</button>
        </div>
        <div class="mcp-guide-note">${copy.token.installNote}</div>
      </div>

      <div class="status-card mcp-guide-card span-2">
        <div class="panel-header small">
          <h3>${copy.flow.step2Title}</h3>
          <p>${copy.flow.step2Desc}</p>
        </div>
        <pre class="mcp-guide-code" id="mcpMasterPromptText"><code>${escapeHtml(copy.flow.masterPrompt)}</code></pre>
        <div class="mcp-guide-actions">
          <button type="button" id="mcpMasterPromptCopyBtn" class="btn-primary">${copy.flow.masterPromptButton}</button>
          <span id="mcpGuideStatus" class="mcp-guide-status" role="status"></span>
        </div>
        <div class="mcp-guide-note">${copy.flow.masterNote}</div>
      </div>
    </div>

    <details class="mcp-guide-advanced">
      <summary>${copy.flow.advancedSummary}</summary>
      <div class="tab-grid monitor-grid mcp-guide-grid">
        <div class="status-card mcp-guide-card span-2">
          <div class="panel-header small">
            <h3>${copy.keys.title}</h3>
            <p>${copy.keys.desc}</p>
          </div>
          <div class="mcp-guide-actions">
            <input type="text" id="mcpKeyLabel" class="mcp-guide-input" placeholder="${escapeHtml(copy.keys.labelPlaceholder)}" />
            <select id="mcpKeyTtl" class="mcp-guide-input">
              <option value="90">90d</option>
              <option value="30">30d</option>
              <option value="365">365d</option>
              <option value="0">${copy.keys.never}</option>
            </select>
            <button type="button" id="mcpKeyCreateBtn" class="btn-primary">${copy.keys.createBtn}</button>
            <span id="mcpKeyStatus" class="mcp-guide-status" role="status"></span>
          </div>
          <pre class="mcp-guide-code" id="mcpKeyNew" hidden></pre>
          <div id="mcpKeysList" class="mcp-guide-list"></div>
          <div class="mcp-guide-note">${copy.keys.note}</div>
        </div>

        <div class="status-card mcp-guide-card">
          <div class="panel-header small">
            <h3>${copy.endpoint.title}</h3>
            <p>${copy.endpoint.description}</p>
          </div>
          ${renderList(copy.endpoint.items)}
        </div>

        <div class="status-card mcp-guide-card">
          <div class="panel-header small">
            <h3>${copy.config.title}</h3>
            <p>${copy.config.description}</p>
          </div>
          ${renderCodeBlock(buildMcpJsonSnippet(endpointUrl))}
          <div class="mcp-guide-actions">
            <button type="button" id="mcpTokenCopyBtn" class="btn-secondary">${copy.token.copyButton}</button>
          </div>
        </div>

        <div class="status-card mcp-guide-card span-2">
          <div class="panel-header small">
            <h3>${copy.token.title}</h3>
            <p>${copy.token.description}</p>
          </div>
          ${renderList(copy.token.items)}
          <div class="mcp-guide-note">${copy.token.note}</div>
        </div>

        <div class="status-card mcp-guide-card">
          <div class="panel-header small">
            <h3>${copy.codex.title}</h3>
            <p>${copy.codex.description}</p>
          </div>
          ${renderSteps(copy.codex.steps)}
        </div>

        <div class="status-card mcp-guide-card">
          <div class="panel-header small">
            <h3>${copy.verify.title}</h3>
            <p>${copy.verify.description}</p>
          </div>
          ${renderSteps(copy.verify.steps)}
        </div>

        <div class="status-card mcp-guide-card">
          <div class="panel-header small">
            <h3>${copy.prompts.title}</h3>
            <p>${copy.prompts.description}</p>
          </div>
          ${renderCodeBlock(buildPromptSnippet(copy.prompts.examples))}
        </div>
      </div>
    </details>
  `;
  return markup.replaceAll(MCP_ENDPOINT_TOKEN, escapeHtml(endpointUrl));
}
