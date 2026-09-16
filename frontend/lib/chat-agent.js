// Pure helpers for the chatbot agent turn (build request, parse response).
// No DOM, no network. History items use {role:"user"|"ai", text}.

export const NAVIGABLE_PAGES = [
  "home", "fast", "advanced", "evolution", "studio", "monitor", "rounds", "analyze",
];

export function buildChatSendPayload(cfg, history, snapshot) {
  const provider = (cfg && cfg.provider) || "";
  const messages = (Array.isArray(history) ? history : []).map((h) => ({
    role: h && h.role === "user" ? "user" : "assistant",
    content: String((h && h.text) || ""),
  }));
  return {
    provider,
    model: (cfg && cfg.model) || "",
    api_key: (cfg && cfg.keys && cfg.keys[provider]) || "",
    messages,
    context: {
      tab: (snapshot && snapshot.tab) || "",
      run_id: (snapshot && (snapshot.runId || snapshot.run_id)) || "",
      lang: (snapshot && snapshot.lang) || "en",
    },
  };
}

export function parseChatSendResult(res) {
  if (res && res.error) {
    return { reply: "", actions: [], error: res.error };
  }
  return {
    reply: (res && res.reply) || "",
    actions: (res && Array.isArray(res.actions)) ? res.actions : [],
  };
}

export function navigateActions(actions) {
  return (Array.isArray(actions) ? actions : []).filter(
    (a) => a && a.type === "navigate" && NAVIGABLE_PAGES.includes(a.page),
  );
}

const _ADV_INT = { num_seq_per_tier: [1, 8], bioemu_num_samples: [1, 50] };
const _ADV_BOOL = ["bioemu_use", "surrogate_triage_enabled"];

export function sanitizeAdvancedAnswers(answers) {
  const src = answers && typeof answers === "object" ? answers : {};
  const out = {};
  for (const [k, [lo, hi]] of Object.entries(_ADV_INT)) {
    if (k in src) {
      const n = Number(src[k]);
      if (Number.isFinite(n)) out[k] = Math.min(hi, Math.max(lo, Math.round(n)));
    }
  }
  for (const k of _ADV_BOOL) {
    if (k in src) out[k] = Boolean(src[k]);
  }
  return out;
}

export function runActions(actions) {
  return (Array.isArray(actions) ? actions : []).filter((a) => a && a.type === "run");
}

export function configureActions(actions) {
  return (Array.isArray(actions) ? actions : [])
    .filter((a) => a && a.type === "configure")
    .map((a) => {
      const clean = { type: "configure", answers: sanitizeAdvancedAnswers(a.answers) };
      if (a.prefill && a.prefill.attachment) clean.prefill = { attachment: a.prefill.attachment };
      if (typeof a.target_text === "string" && a.target_text) clean.target_text = a.target_text;
      return clean;
    });
}
