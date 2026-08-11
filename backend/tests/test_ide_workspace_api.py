from __future__ import annotations

from fastapi.testclient import TestClient

from backend import main
from backend.core.ide_workspace import IDEWorkspaceStore


def test_ide_files_api_round_trip_and_conflict(tmp_path, monkeypatch) -> None:
    store = IDEWorkspaceStore(tmp_path / "ide-workspace.db")
    monkeypatch.setattr(main.app.state, "ide_workspace", store, raising=False)
    client = TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})

    empty = client.get("/api/v1/ide/files", params={"project": "demo"})
    assert empty.status_code == 200
    assert empty.json() == {
        "project": "demo",
        "files": {},
        "revision": 0,
        "updated_at": 0.0,
    }

    saved = client.put(
        "/api/v1/ide/files",
        json={
            "project": "demo",
            "files": {"index.js": "console.log('backend')"},
            "base_revision": 0,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    stale = client.put(
        "/api/v1/ide/files",
        json={
            "project": "demo",
            "files": {"index.js": "stale"},
            "base_revision": 0,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_revision"] == 1

    loaded = client.get("/api/v1/ide/files", params={"project": "demo"})
    assert loaded.json()["files"] == {"index.js": "console.log('backend')"}


def test_ide_files_api_rejects_unsafe_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        main.app.state,
        "ide_workspace",
        IDEWorkspaceStore(tmp_path / "ide-workspace.db"),
        raising=False,
    )
    client = TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})

    response = client.put(
        "/api/v1/ide/files",
        json={"project": "demo", "files": {"../escape.txt": "no"}},
    )

    assert response.status_code == 422
