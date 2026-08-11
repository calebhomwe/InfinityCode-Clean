from __future__ import annotations

import pytest

from backend.core.ide_workspace import (
    IDEWorkspaceError,
    IDEWorkspaceStore,
    RevisionConflict,
)


def test_workspace_round_trip_survives_a_new_store_instance(tmp_path) -> None:
    db_path = tmp_path / "ide-workspace.db"
    first = IDEWorkspaceStore(db_path)

    saved = first.save(
        "default",
        {"index.js": "console.log('persisted')", "src/app.js": "export default 1"},
        base_revision=0,
    )

    assert saved["revision"] == 1
    second = IDEWorkspaceStore(db_path)
    loaded = second.get("default")
    assert loaded["files"] == {
        "index.js": "console.log('persisted')",
        "src/app.js": "export default 1",
    }
    assert loaded["revision"] == 1
    assert loaded["updated_at"] > 0


@pytest.mark.parametrize(
    "bad_path",
    ["../secret.txt", "src/../../secret.txt", "/absolute.txt", "C:\\secret.txt", ""],
)
def test_workspace_rejects_paths_that_escape_the_virtual_project(tmp_path, bad_path) -> None:
    store = IDEWorkspaceStore(tmp_path / "ide-workspace.db")

    with pytest.raises(IDEWorkspaceError):
        store.save("default", {bad_path: "nope"}, base_revision=0)


def test_workspace_uses_optimistic_revisions_to_prevent_silent_overwrites(tmp_path) -> None:
    store = IDEWorkspaceStore(tmp_path / "ide-workspace.db")
    store.save("default", {"index.js": "first"}, base_revision=0)

    with pytest.raises(RevisionConflict) as conflict:
        store.save("default", {"index.js": "stale"}, base_revision=0)

    assert conflict.value.current_revision == 1
    assert store.get("default")["files"]["index.js"] == "first"


def test_workspace_rejects_oversized_payloads(tmp_path) -> None:
    store = IDEWorkspaceStore(
        tmp_path / "ide-workspace.db",
        max_file_bytes=16,
        max_total_bytes=24,
    )

    with pytest.raises(IDEWorkspaceError, match="size"):
        store.save("default", {"large.txt": "x" * 17}, base_revision=0)

    with pytest.raises(IDEWorkspaceError, match="project size"):
        store.save(
            "default",
            {"one.txt": "x" * 13, "two.txt": "y" * 13},
            base_revision=0,
        )
