from assistant_api.config import Settings
from assistant_api.main import create_app
from fastapi.testclient import TestClient


def test_external_feeds_toggle_is_admin_protected_and_persistent(tmp_path):
    token = "test-admin-token-for-local-test-only"
    settings = Settings(tmp_path, "http://ollama", "daily", "research", token, False)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/admin/settings/external-feeds").status_code in (401, 403)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/admin/settings/external-feeds", headers=headers).json() == {"enabled": False}
        response = client.post("/api/admin/settings/external-feeds", headers=headers, json={"enabled": True})
        assert response.json()["enabled"] is True
        assert client.get("/api/admin/settings/external-feeds", headers=headers).json() == {"enabled": True}


def test_local_trust_allows_admin_without_token(tmp_path):
    settings = Settings(tmp_path, "http://ollama", "daily", "research", "", False, True)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/admin/settings/external-feeds").json() == {"enabled": False}
