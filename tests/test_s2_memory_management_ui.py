"""S2 memory UI and API acceptance: exact-key update/delete and owner isolation."""
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Settings
from apps.api.main import create_app


def test_memory_management_api_exact_keys_and_isolation(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path/'memory_management.db'}",
        user_tokens="a:alice,b:bob", allow_demo_identity=False,
        model_provider="fake")
    a = {"Authorization": "Bearer a"}
    b = {"Authorization": "Bearer b"}
    with TestClient(create_app(settings)) as client:
        for key, value in (("test_preference", "蓝色"), ("测试偏好", "绿色")):
            result = client.put("/memories/" + key, headers=a, json={"value": value})
            assert result.status_code == 200
        read = client.get("/memories", headers=a)
        assert read.status_code == 200
        assert {item["key"]: item["value"] for item in read.json()} == {
            "test_preference": "蓝色", "测试偏好": "绿色"}
        assert client.get("/memories", headers=b).json() == []
        assert client.get("/memories").status_code == 401
        updated = client.put("/memories/测试偏好", headers=a, json={"value": "紫色"})
        assert updated.status_code == 200
        assert {item["key"]: item["value"] for item in client.get("/memories", headers=a).json()} == {
            "test_preference": "蓝色", "测试偏好": "紫色"}
        assert client.delete("/memories/test_preference", headers=b).status_code == 404
        assert client.delete("/memories/test_preference", headers=a).status_code == 200
        assert client.delete("/memories/test_preference", headers=a).status_code == 404
        assert client.get("/memories", headers=a).json() == [
            {"key": "测试偏好", "value": "紫色"}]


def test_memory_panel_has_safe_explicit_management_controls():
    html = (Path(__file__).resolve().parents[1] / "apps/web/index.html").read_text(
        encoding="utf-8")
    assert 'id="memory-list"' in html
    assert 'id="memory-refresh"' in html
    assert "encodeURIComponent(item.key)" in html
    assert "method:'PUT'" in html and "method:'DELETE'" in html
    assert "confirm('确定删除长期记忆" in html
    assert "line.append(key,value,save,remove)" in html
    assert "不同 Key 不会自动合并" in html
