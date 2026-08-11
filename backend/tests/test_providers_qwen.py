from pathlib import Path

from backend.core.providers import ProviderManager


def test_dashscope_key_is_masked_and_reported_configured(tmp_path: Path) -> None:
    manager = ProviderManager(tmp_path / "providers.json")
    manager.save({"dashscope_key": "sk-qwen-example-secret"})

    public = manager.public()

    assert public["dashscope_key"] != "sk-qwen-example-secret"
    assert public["configured"]["dashscope"] is True


def test_dashscope_connection_test_uses_bearer_key(tmp_path: Path, monkeypatch) -> None:
    manager = ProviderManager(tmp_path / "providers.json")
    manager.save({"dashscope_key": "sk-qwen-example-secret"})
    seen = {}

    def fake_get(url: str, headers=None, timeout: int = 8) -> int:
        seen.update(url=url, headers=headers, timeout=timeout)
        return 200

    monkeypatch.setattr(manager, "_get", fake_get)

    result = manager.test("dashscope")

    assert result["ok"] is True
    assert "compatible-mode/v1/models" in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer sk-qwen-example-secret"
