from __future__ import annotations

from pipeline_mcp.auth import AuthConfig
from pipeline_mcp.auth import AuthManager


def test_external_users_can_be_listed_and_approved(tmp_path):
    manager = AuthManager(
        config=AuthConfig(
            enabled=True,
            store_path=tmp_path / "users.json",
            secret_path=tmp_path / "secret.key",
            token_ttl_s=3600,
        ),
        users={},
        secret=b"test-secret",
    )
    manager.ensure_admin("admin", "admin-password")

    pending = manager.resolve_external_user(
        {
            "username": "new.user@example.org",
            "role": "user",
            "email": "new.user@example.org",
            "subject": "google-oauth2|123",
        },
        default_status="pending",
    )
    assert pending["status"] == "pending"

    users = manager.list_users()
    assert [user["username"] for user in users] == ["admin", "new.user@example.org"]
    assert users[1]["email"] == "new.user@example.org"
    assert users[1]["external"] is True

    approved = manager.update_user(
        username="new.user@example.org",
        role="model_manager",
        status="approved",
    )
    assert approved["role"] == "model_manager"
    assert approved["status"] == "approved"


def test_issue_token_round_trips_through_verify(tmp_path):
    from pipeline_mcp.auth import AuthManager, AuthConfig

    manager = AuthManager(
        config=AuthConfig(
            enabled=True,
            store_path=tmp_path / "users.json",
            secret_path=tmp_path / "secret.bin",
            token_ttl_s=3600,
        ),
        users={},
        secret=b"test-secret-for-issue",
    )
    created = manager.create_user(username="carol", password="pw123456", role="user")

    issued = manager.issue_token(created)
    assert issued["token"]
    assert issued["expires_at"] > 0

    verified = manager.verify_token(issued["token"])
    assert verified is not None
    assert verified["username"] == "carol"
