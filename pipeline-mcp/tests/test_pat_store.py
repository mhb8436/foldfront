from __future__ import annotations

import json
import time

from pipeline_mcp.pat_store import PatConfig
from pipeline_mcp.pat_store import PatStore
from pipeline_mcp.pat_store import looks_like_pat


def _store(tmp_path, ttl_days=90):
    return PatStore(PatConfig(store_path=tmp_path / "mcp_keys.json", default_ttl_days=ttl_days))


def test_create_returns_raw_token_once_and_verify_round_trips(tmp_path):
    store = _store(tmp_path)
    created = store.create_key({"username": "alice", "role": "user"}, label="laptop")
    assert created["token"].startswith("kbfpat_")
    assert created["label"] == "laptop"
    assert created["expires_at"] > int(time.time())

    user = store.verify(created["token"])
    assert user == {"username": "alice", "role": "user", "auth_type": "pat"}


def test_raw_token_is_never_persisted_plaintext(tmp_path):
    store = _store(tmp_path)
    created = store.create_key({"username": "bob", "role": "user"})
    raw = created["token"]
    on_disk = (tmp_path / "mcp_keys.json").read_text(encoding="utf-8")
    assert raw not in on_disk
    # Only the hash is stored.
    assert "token_hash" in json.loads(on_disk)[created["id"]]


def test_verify_rejects_unknown_and_non_pat_tokens(tmp_path):
    store = _store(tmp_path)
    assert store.verify("kbfpat_does-not-exist") is None
    assert store.verify("eyJ.some.jwt") is None
    assert store.verify("") is None
    assert looks_like_pat("kbfpat_x") is True
    assert looks_like_pat("eyJ") is False


def test_expired_token_is_rejected(tmp_path):
    store = _store(tmp_path)
    created = store.create_key({"username": "carol", "role": "user"}, ttl_days=1)
    # Force expiry in the stored record.
    store._keys[created["id"]]["expires_at"] = int(time.time()) - 10
    assert store.verify(created["token"]) is None


def test_ttl_days_zero_means_never_expires(tmp_path):
    store = _store(tmp_path)
    created = store.create_key({"username": "dave", "role": "user"}, ttl_days=0)
    assert created["expires_at"] == 0
    assert store.verify(created["token"]) is not None


def test_list_is_scoped_to_user_and_revoke_works(tmp_path):
    store = _store(tmp_path)
    a = store.create_key({"username": "alice", "role": "user"}, label="a1")
    store.create_key({"username": "bob", "role": "user"}, label="b1")

    alice_keys = store.list_keys("alice")
    assert [k["label"] for k in alice_keys] == ["a1"]
    assert "token" not in alice_keys[0] and "token_hash" not in alice_keys[0]

    # Bob cannot revoke Alice's key.
    assert store.revoke("bob", a["id"]) is False
    # Alice can.
    assert store.revoke("alice", a["id"]) is True
    assert store.list_keys("alice") == []


def test_store_persists_across_instances(tmp_path):
    store = _store(tmp_path)
    created = store.create_key({"username": "erin", "role": "admin"})
    reopened = _store(tmp_path)
    user = reopened.verify(created["token"])
    assert user is not None and user["username"] == "erin" and user["role"] == "admin"
