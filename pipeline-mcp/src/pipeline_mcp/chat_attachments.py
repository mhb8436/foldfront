"""Persist chatbot file/folder attachments and summarize them for the model.

Attachments arrive as [{name, base64}] (name may be a relative path for folders).
Saved under <output_root>/_chat_uploads/<session_id>/<sanitized relpath>. Path
traversal is neutralized; per-file and per-request size caps are enforced.
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path
from uuid import uuid4

_MAX_FILE_BYTES = 10 * 1024 * 1024      # 10 MB per file
_MAX_TOTAL_BYTES = 50 * 1024 * 1024     # 50 MB per request
_TEXT_EXT = {".txt", ".fasta", ".fa", ".faa", ".seq", ".pdb", ".cif", ".csv",
             ".tsv", ".json", ".md", ".a3m", ".sto", ".log", ".yaml", ".yml"}
_PREVIEW_CHARS = 2000


def _sanitize_relpath(name: str) -> Path:
    raw = str(name or "").replace("\\", "/").strip().lstrip("./")
    parts = [p for p in raw.split("/") if p and p != ".."]
    if not parts:
        parts = [f"upload-{uuid4().hex}"]
    return Path(*parts)


def _session_dir(output_root, session_id) -> Path:
    sid = "".join(c if (c.isalnum() or c in "-_") else "_"
                  for c in str(session_id or "").strip())[:80] or "default"
    return Path(output_root) / "_chat_uploads" / sid


def _text_preview(rel: Path, data: bytes):
    if rel.suffix.lower() not in _TEXT_EXT:
        return None
    return data.decode("utf-8", errors="replace")[:_PREVIEW_CHARS]


def save_chat_attachments(output_root, session_id, attachments) -> list[dict]:
    """Save [{name, base64}] and return [{name, size, preview?}]."""
    saved: list[dict] = []
    total = 0
    base = _session_dir(output_root, session_id)
    for att in attachments or []:
        name = str((att or {}).get("name") or "").strip()
        b64 = str((att or {}).get("base64") or "")
        if not name or not b64:
            continue
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(data) > _MAX_FILE_BYTES:
            continue
        total += len(data)
        if total > _MAX_TOTAL_BYTES:
            break
        rel = _sanitize_relpath(name)
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entry = {"name": str(rel).replace("\\", "/"), "size": len(data)}
        preview = _text_preview(rel, data)
        if preview is not None:
            entry["preview"] = preview
        saved.append(entry)
    return saved


def list_chat_attachments(output_root, session_id) -> list[dict]:
    base = _session_dir(output_root, session_id)
    out: list[dict] = []
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        if p.is_file():
            out.append({"name": str(p.relative_to(base)).replace("\\", "/"),
                        "size": p.stat().st_size})
    return out


def attachment_prompt_note(saved: list[dict]) -> str:
    if not saved:
        return ""
    lines = ["The user attached these files (saved on the server):"]
    for s in saved:
        lines.append(f"- {s['name']} ({s.get('size', 0)} bytes)")
        if s.get("preview"):
            lines.append(f"  preview:\n{s['preview']}")
    return "\n".join(lines)


_STRUCT_EXT = {".pdb", ".ent", ".cif", ".mmcif"}


def summarize_structure(name: str, text: str) -> str | None:
    """Best-effort one-line summary of a structure file: title, chain IDs, ligands.
    PDB/ENT parsed by column; CIF returns the title only (best-effort)."""
    ext = Path(name).suffix.lower()
    if ext not in _STRUCT_EXT:
        return None
    lines = str(text or "").splitlines()
    if ext in {".pdb", ".ent"}:
        title_parts: list[str] = []
        chains: list[str] = []
        seen: set[str] = set()
        ligands: set[str] = set()
        for ln in lines[:200000]:
            rec = ln[:6].strip()
            if rec == "TITLE":
                title_parts.append(ln[10:].strip())
            elif rec in ("ATOM", "HETATM"):
                ch = ln[21:22].strip()
                if ch and ch not in seen:
                    seen.add(ch)
                    chains.append(ch)
                if rec == "HETATM":
                    res = ln[17:20].strip()
                    if res and res not in ("HOH", "WAT"):
                        ligands.add(res)
        parts: list[str] = []
        title = " ".join(title_parts).strip()
        if title:
            parts.append(f"title: {title}")
        if chains:
            parts.append(f"chains: {', '.join(chains)}")
        if ligands:
            parts.append(f"ligands/hetero: {', '.join(sorted(ligands))}")
        return "; ".join(parts) if parts else None
    # .cif / .mmcif: best-effort title only
    for ln in lines[:5000]:
        low = ln.strip().lower()
        if low.startswith("_struct.title"):
            rest = ln.strip()[len("_struct.title"):].strip().strip("'\"")
            return f"title: {rest}" if rest else None
    return None


_TARGET_PRIORITY = (".pdb", ".ent", ".cif", ".mmcif", ".fasta", ".fa", ".faa", ".seq")


def primary_target_text(output_root, session_id, *, max_chars: int = 2_000_000) -> str | None:
    """Decoded text of the session's primary target file (structure/sequence first),
    so the frontend can pre-fill the target even if the browser lost the upload."""
    base = _session_dir(output_root, session_id)
    if not base.exists():
        return None
    files = [p for p in sorted(base.rglob("*")) if p.is_file()]

    def rank(p: Path) -> int:
        ext = p.suffix.lower()
        return _TARGET_PRIORITY.index(ext) if ext in _TARGET_PRIORITY else len(_TARGET_PRIORITY)

    for p in sorted(files, key=rank):
        if p.suffix.lower() in _TEXT_EXT:
            try:
                return p.read_bytes().decode("utf-8", errors="replace")[:max_chars]
            except OSError:
                continue
    return None


def session_attachment_context(output_root, session_id, *, max_files: int = 3,
                               preview_chars: int = 1200) -> str:
    """Context block for ALL files saved in the session (not just this turn), so the
    assistant can describe the attached target on any turn. Includes a structure
    summary (chains/title/ligands) and a truncated preview for text files."""
    base = _session_dir(output_root, session_id)
    if not base.exists():
        return ""
    files = [p for p in sorted(base.rglob("*")) if p.is_file()][:max_files]
    if not files:
        return ""
    out = ["The user attached these files this session. You CAN read the content and "
           "summary below to describe the target (title, chains, ligands, sequence). "
           "You cannot browse external websites (e.g. RCSB)."]
    for p in files:
        rel = str(p.relative_to(base)).replace("\\", "/")
        out.append(f"- {rel} ({p.stat().st_size} bytes)")
        try:
            text = p.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        summ = summarize_structure(rel, text)
        if summ:
            out.append(f"  summary: {summ}")
        if Path(rel).suffix.lower() in _TEXT_EXT:
            out.append("  preview:\n" + text[:preview_chars])
    return "\n".join(out)
