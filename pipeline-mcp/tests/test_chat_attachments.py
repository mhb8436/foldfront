import base64
from pipeline_mcp.chat_attachments import (
    save_chat_attachments, list_chat_attachments, attachment_prompt_note)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_save_and_list_roundtrip(tmp_path):
    saved = save_chat_attachments(tmp_path, "sess1", [
        {"name": "seq.fasta", "base64": _b64(b">a\nACDEFG")},
    ])
    assert saved[0]["name"] == "seq.fasta"
    assert saved[0]["size"] == 9
    assert saved[0]["preview"].startswith(">a")
    listed = list_chat_attachments(tmp_path, "sess1")
    assert [x["name"] for x in listed] == ["seq.fasta"]
    # actually written to disk under _chat_uploads/sess1/
    assert (tmp_path / "_chat_uploads" / "sess1" / "seq.fasta").read_bytes() == b">a\nACDEFG"


def test_folder_relative_paths_preserved(tmp_path):
    saved = save_chat_attachments(tmp_path, "sess2", [
        {"name": "proj/a/x.txt", "base64": _b64(b"hi")},
        {"name": "proj/b/y.txt", "base64": _b64(b"yo")},
    ])
    names = sorted(s["name"] for s in saved)
    assert names == ["proj/a/x.txt", "proj/b/y.txt"]
    assert (tmp_path / "_chat_uploads" / "sess2" / "proj" / "a" / "x.txt").exists()


def test_path_traversal_is_neutralized(tmp_path):
    saved = save_chat_attachments(tmp_path, "s", [
        {"name": "../../etc/evil", "base64": _b64(b"x")},
    ])
    # '..' stripped; file stays under the session dir
    p = tmp_path / "_chat_uploads" / "s" / "etc" / "evil"
    assert p.exists()
    assert saved[0]["name"] == "etc/evil"


def test_invalid_base64_skipped(tmp_path):
    saved = save_chat_attachments(tmp_path, "s", [{"name": "b.bin", "base64": "!!!notb64"}])
    assert saved == []


def test_per_file_size_cap(tmp_path):
    big = _b64(b"x" * (10 * 1024 * 1024 + 1))
    saved = save_chat_attachments(tmp_path, "s", [{"name": "big.bin", "base64": big}])
    assert saved == []


def test_binary_has_no_preview(tmp_path):
    saved = save_chat_attachments(tmp_path, "s", [{"name": "a.png", "base64": _b64(b"\x89PNG\x00")}])
    assert "preview" not in saved[0]


def test_attachment_prompt_note():
    note = attachment_prompt_note([{"name": "seq.fasta", "size": 8, "preview": ">a\nACDEFG"}])
    assert "seq.fasta" in note and "ACDEFG" in note
    assert attachment_prompt_note([]) == ""


def _pdb(rec, res, chain):
    # place resName at cols 18-20 (idx 17:20) and chain ID at col 22 (idx 21)
    return (rec.ljust(6) + " " * 11)[:17] + res.ljust(3)[:3] + " " + chain[:1]


_PDB_TEXT = "\n".join([
    "TITLE     NPU DNAE INTEIN",
    _pdb("ATOM", "MET", "A"),
    _pdb("ATOM", "ALA", "A"),
    _pdb("ATOM", "GLY", "B"),
    _pdb("HETATM", "HOH", "A"),
    _pdb("HETATM", "HEM", "B"),
])


def test_summarize_structure_pdb():
    from pipeline_mcp.chat_attachments import summarize_structure
    summ = summarize_structure("4kl5.pdb", _PDB_TEXT)
    assert "title: NPU DNAE INTEIN" in summ
    assert "chains: A, B" in summ
    assert "HEM" in summ and "HOH" not in summ  # water excluded


def test_summarize_structure_ignores_non_structure():
    from pipeline_mcp.chat_attachments import summarize_structure
    assert summarize_structure("a.txt", "hello") is None


def test_session_attachment_context_includes_summary(tmp_path):
    from pipeline_mcp.chat_attachments import save_chat_attachments, session_attachment_context
    save_chat_attachments(tmp_path, "s9", [{"name": "4kl5.pdb", "base64": _b64(_PDB_TEXT.encode())}])
    ctx = session_attachment_context(tmp_path, "s9")
    assert "4kl5.pdb" in ctx
    assert "chains: A, B" in ctx
    assert ctx == "" or "cannot browse external" in ctx  # guidance present when non-empty


def test_primary_target_text_prefers_structure(tmp_path):
    from pipeline_mcp.chat_attachments import save_chat_attachments, primary_target_text
    save_chat_attachments(tmp_path, "pt", [
        {"name": "notes.txt", "base64": _b64(b"hello notes")},
        {"name": "4kl5.pdb", "base64": _b64(_PDB_TEXT.encode())},
    ])
    txt = primary_target_text(tmp_path, "pt")
    assert txt is not None and txt.startswith("TITLE") and "MET" in txt  # the .pdb, not notes.txt
