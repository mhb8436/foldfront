"""Provider-agnostic chat tool-calling loop for the site AI chatbot.

The loop (`run_chat_turn`) is provider-neutral: it calls `_complete`, which
dispatches to a raw-HTTP adapter per provider. Read tools run server-side via an
injected `tool_executor`; a `navigate` call is returned as a client action.
API keys are used transiently and never logged (errors are redacted).
"""
from __future__ import annotations

import json
import os
import re

import requests

from .chat_providers import ChatProviderError, _normalize_provider

# Self-hosted local LLM (EXAONE via vLLM, OpenAI-compatible, no auth). Configurable
# via env; defaults point at the verified in-cluster endpoint / served model id.
_LOCAL_LLM_DEFAULT = "http://211.188.35.221:8000/v1"
_LOCAL_LLM_MODEL = "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"


def _local_llm_base() -> str:
    """Base URL for the local LLM (env LOCAL_LLM_URL, falling back to default).
    Only http/https URLs are accepted; production should point this at an https
    (TLS-terminated) endpoint — plaintext http is for in-cluster/dev use only."""
    base = (os.environ.get("LOCAL_LLM_URL") or _LOCAL_LLM_DEFAULT).rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ChatProviderError("upstream", "LOCAL_LLM_URL must be an http(s) URL")
    return base

READ_TOOLS = ("pipeline_status", "pipeline_queue_eta",
              "pipeline_list_runs", "pipeline_list_artifacts")
NAVIGATE_PAGES = ("home", "fast", "advanced", "evolution",
                  "studio", "monitor", "rounds", "analyze")


def tool_specs() -> list[dict]:
    """Common tool definitions the model sees: read tools + navigate."""
    run_id = {"type": "object", "properties": {"run_id": {"type": "string",
              "description": "run id; optional, defaults to the latest run"}}}
    return [
        {"name": "pipeline_status", "description": "Get the state/stage of a run.",
         "parameters": run_id},
        {"name": "pipeline_queue_eta", "description": "Approximate worker-queue wait/finish ETA for a run.",
         "parameters": run_id},
        {"name": "pipeline_list_runs", "description": "List recent runs.",
         "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
        {"name": "pipeline_list_artifacts", "description": "List a run's output artifacts.",
         "parameters": run_id},
        {"name": "navigate", "description": "Take the user to a workspace page. "
         "Use this to guide the user to start a run (they click the run button themselves). "
         "To run an attached file, navigate to 'fast' with prefill={\"attachment\": \"<the file name>\"} "
         "so the file is pre-loaded as the target; the user then clicks Run.",
         "parameters": {"type": "object",
                        "properties": {
                            "page": {"type": "string", "enum": list(NAVIGATE_PAGES)},
                            "prefill": {"type": "object",
                                        "properties": {"attachment": {"type": "string"}}},
                        },
                        "required": ["page"]}},
        {"name": "configure_advanced",
         "description": "Recommend and pre-fill core Advanced parameters for the user to review, then "
         "they click the run button (you never start the run). Provide 'answers' with any of: "
         "num_seq_per_tier (int 1-8, ProteinMPNN sequences per backbone), bioemu_use (bool, enable BioEmu), "
         "bioemu_num_samples (int 1-50), surrogate_triage_enabled (bool). Include prefill "
         "{\"attachment\": \"<file name>\"} to also load an attached target.",
         "parameters": {"type": "object",
                        "properties": {
                            "answers": {"type": "object", "properties": {
                                "num_seq_per_tier": {"type": "integer"},
                                "bioemu_use": {"type": "boolean"},
                                "bioemu_num_samples": {"type": "integer"},
                                "surrogate_triage_enabled": {"type": "boolean"},
                            }},
                            "prefill": {"type": "object",
                                        "properties": {"attachment": {"type": "string"}}},
                        },
                        "required": ["answers"]}},
        {"name": "run_pipeline",
         "description": "Offer the user a one-click Run button in the chat to start the pipeline. Call "
         "this ONLY when the user explicitly asks to start/run it (e.g. 'run it', '실행해줘', '돌려줘'). "
         "It does NOT start the run — the user presses the button. Make sure the target and parameters "
         "are set first (call configure_advanced in the same turn when needed).",
         "parameters": {"type": "object", "properties": {}}},
    ]


def run_chat_turn(provider, model, api_key, messages, tool_executor, *,
                  system=None, max_steps=6, timeout=60.0) -> dict:
    """One user turn. Returns {"reply", "actions", "steps"}.
    Raises ChatProviderError on provider auth/upstream failure."""
    msgs = [dict(m) for m in (messages or [])]
    actions: list[dict] = []
    reply = ""
    steps = 0
    specs = tool_specs()
    taken: set[str] = set()
    for steps in range(1, max_steps + 1):
        result = _complete(provider, model, api_key, msgs, specs, system=system, timeout=timeout)
        reply = result.get("text") or reply
        tool_calls = result.get("tool_calls") or []
        if not tool_calls:
            return {"reply": reply, "actions": actions, "steps": steps}
        msgs.append({"role": "assistant", "content": result.get("text") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            cid = call.get("id") or name
            if name == "navigate":
                if "navigate" in taken:
                    msgs.append({"role": "tool", "tool_call_id": cid, "name": name,
                                 "content": {"ok": True,
                                             "note": "already handled; now explain to the user in text"}})
                    continue
                page = str(args.get("page") or "").strip().lower()
                if page not in NAVIGATE_PAGES:
                    page = "home"
                action = {"type": "navigate", "page": page}
                if isinstance(args.get("prefill"), dict):
                    action["prefill"] = args["prefill"]
                actions.append(action)
                taken.add("navigate")
                msgs.append({"role": "tool", "tool_call_id": cid, "name": name,
                             "content": {"ok": True, "navigated": page,
                                         "note": "navigation done; now explain the next steps to the user in text"}})
            elif name == "configure_advanced":
                if "configure" in taken:
                    msgs.append({"role": "tool", "tool_call_id": cid, "name": name,
                                 "content": {"ok": True,
                                             "note": "already configured; now explain the values and reasons in text"}})
                    continue
                answers = args.get("answers") if isinstance(args.get("answers"), dict) else {}
                action = {"type": "configure", "answers": answers}
                if isinstance(args.get("prefill"), dict):
                    action["prefill"] = args["prefill"]
                actions.append(action)
                taken.add("configure")
                msgs.append({"role": "tool", "tool_call_id": cid, "name": name,
                             "content": {"ok": True, "configured": list(answers.keys()),
                                         "note": "values pre-filled on Advanced; now explain each value and WHY, in text"}})
            elif name == "run_pipeline":
                if "run" not in taken:
                    actions.append({"type": "run"})
                    taken.add("run")
                    msgs.append({"role": "tool", "tool_call_id": cid, "name": name,
                                 "content": {"ok": True, "note": "A Run button was shown to the user; tell them to press it. You did NOT start the run."}})
                else:
                    msgs.append({"role": "tool", "tool_call_id": cid, "name": name,
                                 "content": {"ok": True, "note": "already offered; explain in text"}})
                continue
            elif name in READ_TOOLS:
                out = tool_executor(name, args)
                msgs.append({"role": "tool", "tool_call_id": cid, "name": name, "content": out})
            else:
                msgs.append({"role": "tool", "tool_call_id": cid, "name": name,
                             "content": {"error": "tool not available"}})
        # Do NOT stop after a client action: continue so the model produces its
        # explanation turn (it explains AFTER seeing the tool result). Bounded by max_steps.
    return {"reply": reply or "(stopped after the step limit)", "actions": actions, "steps": steps}


def _post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        detail = re.sub(r"key=[^&\s]+", "key=***", str(exc))
        raise ChatProviderError("upstream", f"request failed: {detail}") from exc
    if resp.status_code in (401, 403):
        raise ChatProviderError("auth", "provider rejected the API key")
    if resp.status_code >= 400:
        body = ""
        try:
            body = re.sub(r"key=[^&\s]+", "key=***", resp.text or "")[:300]
        except Exception:
            body = ""
        detail = f"provider returned HTTP {resp.status_code}"
        if body:
            detail = f"{detail}: {body}"
        raise ChatProviderError("upstream", detail)
    try:
        data = resp.json()
    except ValueError as exc:
        raise ChatProviderError("upstream", "provider returned invalid JSON") from exc
    return data if isinstance(data, dict) else {}


def _complete(provider, model, api_key, messages, tools, *, system=None, timeout=60.0) -> dict:
    canonical = _normalize_provider(provider)
    # Local EXAONE needs no API key — route it before the key requirement below.
    if canonical == "exaone":
        return _exaone_complete(model, messages, tools, system, timeout)
    key = str(api_key or "").strip()
    if not key:
        raise ChatProviderError("auth", "API key is required")
    if canonical == "anthropic":
        return _anthropic_complete(model, key, messages, tools, system, timeout)
    if canonical == "openai":
        return _openai_complete(model, key, messages, tools, system, timeout)
    return _gemini_complete(model, key, messages, tools, system, timeout)


def _to_anthropic_messages(messages):
    out = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": str(m.get("content") or "")}]})
        elif role == "assistant":
            if not m.get("content") and not (m.get("tool_calls") or []):
                continue
            content = []
            if m.get("content"):
                content.append({"type": "text", "text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                content.append({"type": "tool_use", "id": tc.get("id") or tc.get("name"),
                                "name": tc.get("name"), "input": tc.get("args") or {}})
            out.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
        elif role == "tool":
            out.append({"role": "user", "content": [{"type": "tool_result",
                        "tool_use_id": m.get("tool_call_id") or m.get("name"),
                        "content": json.dumps(m.get("content"))}]})
    return out


def _anthropic_complete(model, key, messages, tools, system, timeout):
    body = {
        "model": model, "max_tokens": 1024,
        "messages": _to_anthropic_messages(messages),
        "tools": [{"name": t["name"], "description": t["description"],
                   "input_schema": t["parameters"]} for t in tools],
    }
    if system:
        body["system"] = system
    data = _post_json("https://api.anthropic.com/v1/messages",
                      {"x-api-key": key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}, body, timeout)
    text = ""
    calls = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text += block.get("text") or ""
        elif block.get("type") == "tool_use":
            calls.append({"id": block.get("id"), "name": block.get("name"),
                          "args": block.get("input") or {}})
    return {"text": text, "tool_calls": calls}


def _to_openai_messages(messages, system):
    out = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": str(m.get("content") or "")})
        elif role == "assistant":
            if not m.get("content") and not (m.get("tool_calls") or []):
                continue
            msg = {"role": "assistant", "content": m.get("content") or None}
            tcs = m.get("tool_calls") or []
            if tcs:
                msg["tool_calls"] = [{"id": tc.get("id") or tc.get("name"), "type": "function",
                                      "function": {"name": tc.get("name"),
                                                   "arguments": json.dumps(tc.get("args") or {})}}
                                     for tc in tcs]
            out.append(msg)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id") or m.get("name"),
                        "content": json.dumps(m.get("content"))})
    return out


def _openai_style_complete(model, key, messages, tools, system, timeout, *, base_url):
    """Shared OpenAI wire-format completion. Sends an Authorization header only
    when `key` is non-empty (the local EXAONE endpoint requires no auth)."""
    body = {
        "model": model,
        "messages": _to_openai_messages(messages, system),
        "tools": [{"type": "function", "function": {"name": t["name"],
                   "description": t["description"], "parameters": t["parameters"]}} for t in tools],
        "tool_choice": "auto",
    }
    headers = {"content-type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = _post_json(base_url.rstrip("/") + "/chat/completions", headers, body, timeout)
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append({"id": tc.get("id"), "name": fn.get("name"), "args": args})
    return {"text": text, "tool_calls": calls}


def _openai_complete(model, key, messages, tools, system, timeout):
    return _openai_style_complete(model, key, messages, tools, system, timeout,
                                  base_url="https://api.openai.com/v1")


def _exaone_complete(model, messages, tools, system, timeout):
    """Local EXAONE (vLLM, OpenAI-compatible, keyless). The endpoint serves a
    single model, so ignore any client-supplied model id (it may be a stale
    value carried over from a previously-selected provider, e.g. a Claude/GPT
    id that the local endpoint would 404 on) and always use the configured
    served model. Strips reasoning-model chain-of-thought from the reply."""
    served = os.environ.get("LOCAL_LLM_MODEL") or _LOCAL_LLM_MODEL
    # The local 33B model shares a GPU with the folding workers and is far
    # slower than the elastic commercial APIs; give it a larger read timeout so
    # a single completion under concurrent GPU load doesn't trip the caller's
    # short default (which caused "Read timed out (read timeout=60.0)").
    try:
        local_timeout = float(os.environ.get("LOCAL_LLM_TIMEOUT_S") or 300.0)
    except ValueError:
        local_timeout = 300.0
    local_timeout = max(float(timeout or 0), local_timeout)
    out = _openai_style_complete(
        served, "", messages, tools, system, local_timeout, base_url=_local_llm_base())
    out["text"] = _strip_reasoning(out.get("text") or "")
    return out


def _strip_reasoning(text: str) -> str:
    """EXAONE emits chain-of-thought before its answer. It may be wrapped in
    <think>...</think>, or emitted as leading reasoning terminated by a lone
    </think> with no opening tag. Keep only the text after the final </think>,
    then drop any residual <think>...</think> pairs."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _to_gemini_contents(messages):
    out = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "parts": [{"text": str(m.get("content") or "")}]})
        elif role == "assistant":
            if not m.get("content") and not (m.get("tool_calls") or []):
                continue
            parts = []
            if m.get("content"):
                parts.append({"text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                parts.append({"functionCall": {"name": tc.get("name"), "args": tc.get("args") or {}}})
            out.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif role == "tool":
            out.append({"role": "user", "parts": [{"functionResponse": {
                "name": m.get("name"), "response": {"result": m.get("content")}}}]})
    return out


def _gemini_complete(model, key, messages, tools, system, timeout):
    body = {
        "contents": _to_gemini_contents(messages),
        "tools": [{"function_declarations": [{"name": t["name"], "description": t["description"],
                   "parameters": t["parameters"]} for t in tools]}],
    }
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           f":generateContent?key={key}")
    data = _post_json(url, {"content-type": "application/json"}, body, timeout)
    cand = (data.get("candidates") or [{}])[0]
    parts = ((cand.get("content") or {}).get("parts")) or []
    text = ""
    calls = []
    for i, p in enumerate(parts):
        if "text" in p:
            text += p.get("text") or ""
        fc = p.get("functionCall")
        if fc:
            calls.append({"id": f"{fc.get('name')}-{i}", "name": fc.get("name"),
                          "args": fc.get("args") or {}})
    return {"text": text, "tool_calls": calls}
