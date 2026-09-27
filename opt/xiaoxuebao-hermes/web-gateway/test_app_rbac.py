from __future__ import annotations

import os
import tempfile
from pathlib import Path


_db_path = Path(tempfile.gettempdir()) / "xiaoxuebao-rbac-test.db"
_db_path.unlink(missing_ok=True)
os.environ["XIAOXUEBAO_GATEWAY_DB"] = str(_db_path)
os.environ["WEB_GATEWAY_SESSION_SECRET"] = "rbac-test-session-secret"
os.environ["WEB_GATEWAY_COOKIE_SECURE"] = "false"
os.environ["WEB_GATEWAY_ADMIN_USERNAME"] = ""
os.environ["WEB_GATEWAY_ADMIN_PASSWORD"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402


def _login(client: TestClient, identifier: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"identifier": identifier, "password": password})
    assert response.status_code == 200, response.text


def test_high_risk_commands_are_superadmin_only() -> None:
    assert app.build_hermes_payload([{"role": "user", "content": "你好"}])["tool_choice"] == "none"
    app.db.create_user("13800000001", "Admin1234", "超管", "管理员", admin_role="superadmin")
    app.db.create_user("13800000002", "Admin1234", "运维", "管理员", admin_role="admin")
    app.db.create_user("13800000003", "User1234", "普通用户", "家长")
    client = TestClient(app.app)

    _login(client, "13800000002", "Admin1234")
    assert client.get("/api/admin/stats").status_code == 200
    assert client.get("/api/admin/commands").status_code == 403
    assert client.post("/api/admin/commands/preview", json={"command": "状态"}).status_code == 403
    superadmin_id = app.db.get_user_by_phone("13800000001")["id"]
    assert client.patch(f"/api/admin/users/{superadmin_id}/status", json={"status": "inactive"}).status_code == 403

    client.post("/api/auth/logout")
    _login(client, "13800000001", "Admin1234")
    assert client.patch(f"/api/admin/users/{superadmin_id}/status", json={"status": "inactive"}).status_code == 409
    preview = client.post("/api/admin/commands/preview", json={"command": "状态"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["action"] == "ability_pack_status"

    client.post("/api/auth/logout")
    _login(client, "13800000003", "User1234")
    assert client.get("/api/admin/stats").status_code == 403
    assert client.post("/api/admin/commands/preview", json={"command": "状态"}).status_code == 403
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["admin_role"] == "user"
