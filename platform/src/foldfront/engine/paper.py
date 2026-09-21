"""Extract design constraints from a paper.

Reads a paper's text and asks the LLM which residues must not change - catalytic
sites, binding interfaces, conserved motifs - so they can be held fixed in
design. This is the console-side path; the same capability is also one of the
original MCP tools (tools._analyze_paper_for_masking), so an agent connected
over MCP reaches it too. The masks are suggestions the researcher reviews, not
ground truth: the reply says so, and the residue picker shows them for approval.
"""

from __future__ import annotations

import io
import json
from typing import Any, Awaitable, Callable

#  The paper's text is clipped before it goes to the model - a long paper past
#  this adds latency and context without adding constraints.
MAX_PAPER_CHARS = 120_000

_SYSTEM = (
    "You are an expert structural biologist. Read the research paper and extract "
    "structural constraints for protein design. Identify residues that MUST NOT be "
    "mutated (catalytic triads, binding interfaces, conserved motifs). "
    "Return ONLY a valid JSON object with this schema:\n"
    "{\n"
    '  "suggested_masks": [\n'
    '    {"chain": "A", "residue_index": 64, "residue_name": "HIS", '
    '"label": "short label", "evidence": "exact quote from the paper", '
    '"confidence": "high" | "low"}\n'
    "  ]\n"
    "}\n"
    "Use confidence \"low\" when the numbering may not match the given sequence. "
    "If the paper names no such residues, return an empty list."
)


class PaperError(RuntimeError):
    pass


def extract_text(pdf_bytes: bytes) -> str:
    """Pull the text out of a PDF. Raises PaperError if it cannot be read."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises a variety of errors
        raise PaperError(f"PDF 를 읽지 못했습니다: {exc}") from exc
    text = text.strip()
    if not text:
        raise PaperError("PDF 에서 글자를 찾지 못했습니다 (스캔 이미지일 수 있습니다)")
    return text


def _strip_fences(text: str) -> str:
    """Models often wrap JSON in a ```json fence; take what is inside."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def masks_from_reply(reply: str) -> list[dict[str, Any]]:
    """Parse the model's reply into normalised masks. A reply that is not the
    agreed JSON yields no masks rather than an error - a stage that ran on a
    paper the model could not use is not a failure."""
    try:
        parsed = json.loads(_strip_fences(reply))
    except (json.JSONDecodeError, ValueError):
        return []
    raw = parsed.get("suggested_masks") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []
    masks: list[dict[str, Any]] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        try:
            resi = int(m.get("residue_index"))
        except (TypeError, ValueError):
            continue
        masks.append(
            {
                "chain": str(m.get("chain") or "A"),
                "residue_index": resi,
                "residue_name": (str(m["residue_name"]) if m.get("residue_name") else None),
                "label": (str(m["label"]) if m.get("label") else None),
                "evidence": (str(m["evidence"]) if m.get("evidence") else None),
                "confidence": "low" if str(m.get("confidence")).lower().startswith("low") else "high",
            }
        )
    return masks


def fixed_positions_from_masks(masks: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Fold the masks down to the {chain: [resi]} shape the design stage takes."""
    out: dict[str, set[int]] = {}
    for m in masks:
        out.setdefault(str(m["chain"]), set()).add(int(m["residue_index"]))
    return {chain: sorted(resis) for chain, resis in out.items() if resis}


async def extract_constraints(
    paper_text: str,
    *,
    target_sequence: str | None = None,
    complete: Callable[[list[dict[str, str]]], Awaitable[str]] | None = None,
) -> dict[str, Any]:
    """Ask the LLM for residues to hold fixed. `complete` is injectable so a
    test does not call a model; by default it uses the console's LLM."""
    if not paper_text.strip():
        raise PaperError("논문 본문이 비어 있습니다")
    if complete is None:
        from foldfront.engine.copilot import complete_openai

        complete = complete_openai

    user = f"Reference Paper Content:\n{paper_text[:MAX_PAPER_CHARS]}\n\n"
    if target_sequence:
        user += f"Target Protein Sequence (for numbering alignment check):\n{target_sequence}\n\n"
    user += "Extract the structural constraints as requested."

    reply = await complete([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}])
    masks = masks_from_reply(reply)
    return {"masks": masks, "fixed_positions": fixed_positions_from_masks(masks)}
