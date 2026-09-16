from __future__ import annotations

import pytest

from pipeline_mcp.bio import structure_fetch
from pipeline_mcp.bio.structure_fetch import resolve_structure_input


class _Resp:
    def __init__(self, text, status=200, location=None):
        self.text = text
        self._status = status
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None
        self.is_permanent_redirect = False

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")


def _mock_get(monkeypatch, recorder):
    def _get(url, timeout=None, allow_redirects=True):
        recorder.append(url)
        return _Resp("HEADER fetched\nATOM  ...\n")
    monkeypatch.setattr(structure_fetch.requests, "get", _get)


def test_raw_multiline_pdb_passthrough(monkeypatch):
    calls = []
    _mock_get(monkeypatch, calls)
    raw = "HEADER    X\nATOM      1  N   MET A   1      11.1  22.2  33.3\n"
    assert resolve_structure_input(raw) == raw
    assert calls == []  # never fetched


def test_pdb_id_fetches_from_rcsb(monkeypatch):
    calls = []
    _mock_get(monkeypatch, calls)
    out = resolve_structure_input("4KL5")
    assert out.startswith("HEADER fetched")
    assert calls == ["https://files.rcsb.org/download/4KL5.pdb"]


def test_pdb_id_case_insensitive(monkeypatch):
    calls = []
    _mock_get(monkeypatch, calls)
    resolve_structure_input("4kl5")
    assert calls == ["https://files.rcsb.org/download/4KL5.pdb"]


def test_allowlisted_url_fetched(monkeypatch):
    calls = []
    _mock_get(monkeypatch, calls)
    url = "https://files.rcsb.org/download/1LVM.pdb"
    out = resolve_structure_input(url)
    assert out.startswith("HEADER fetched")
    assert calls == [url]


def test_disallowed_url_rejected_ssrf(monkeypatch):
    calls = []
    _mock_get(monkeypatch, calls)
    with pytest.raises(ValueError):
        resolve_structure_input("http://127.0.0.1:18080/internal")
    with pytest.raises(ValueError):
        resolve_structure_input("https://evil.example.com/x.pdb")
    assert calls == []  # never fetched a disallowed host


def test_redirect_to_disallowed_host_rejected(monkeypatch):
    # An allowlisted host that 30x-redirects to an internal address must not
    # bypass the allowlist (SSRF redirect bypass).
    calls = []

    def _get(url, timeout=None, allow_redirects=True):
        calls.append(url)
        if url == "https://files.rcsb.org/download/1ABC.pdb":
            return _Resp("", status=302, location="http://169.254.169.254/latest")
        return _Resp("HEADER fetched\n")

    monkeypatch.setattr(structure_fetch.requests, "get", _get)
    with pytest.raises(ValueError):
        resolve_structure_input("1ABC")
    # never followed the redirect to the internal host
    assert "http://169.254.169.254/latest" not in calls


def test_redirect_to_allowed_host_followed(monkeypatch):
    calls = []

    def _get(url, timeout=None, allow_redirects=True):
        calls.append(url)
        if url == "https://files.rcsb.org/download/1ABC.pdb":
            return _Resp(
                "", status=302,
                location="https://files.wwpdb.org/download/1ABC.pdb",
            )
        return _Resp("HEADER fetched\nATOM\n")

    monkeypatch.setattr(structure_fetch.requests, "get", _get)
    out = resolve_structure_input("1ABC")
    assert out.startswith("HEADER fetched")
    assert calls[-1] == "https://files.wwpdb.org/download/1ABC.pdb"


def test_empty_and_non_id_passthrough(monkeypatch):
    calls = []
    _mock_get(monkeypatch, calls)
    assert resolve_structure_input("") == ""
    assert resolve_structure_input("   ") == "   "
    # short non-id, non-url token left as-is (treated as raw/other)
    assert resolve_structure_input("hello") == "hello"
    assert resolve_structure_input(None) is None
    assert calls == []
