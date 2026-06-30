from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api import product_main
from workspace import manager as workspace_manager


def _client(monkeypatch, base: Path) -> TestClient:
    """TestClient with the managed-projects base dir pinned to a tmp folder."""
    monkeypatch.setattr(workspace_manager, "base_dir", lambda *a, **k: base.resolve())
    return TestClient(product_main.app)


def test_managed_project_create_tree_read_write(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    base = tmp_path / "projects"
    client = _client(monkeypatch, base)
    common = {"metadata_db_path": str(db_path)}

    # Create a managed project → folder on disk + DuckDB registration.
    created = client.post("/workspaces", json={"name": "Mein Tool", **common})
    assert created.status_code == 200, created.text
    project = created.json()
    pid = project["id"]
    assert project["kind"] == "managed"
    assert project["exists"] is True
    assert Path(project["path"]).is_dir()
    assert (base / "Mein-Tool") == Path(project["path"])

    # List shows it + the base dir.
    listed = client.get("/workspaces", params=common)
    assert listed.status_code == 200
    body = listed.json()
    assert any(p["id"] == pid for p in body["projects"])
    assert body["base_dir"] == str(base.resolve())

    # Tree contains the seeded README.
    tree = client.get(f"/workspaces/{pid}/tree", params=common)
    assert tree.status_code == 200
    names = {child["name"] for child in tree.json()["children"]}
    assert "README.md" in names

    # Write a new file, then read it back verbatim.
    write = client.put(
        f"/workspaces/{pid}/file",
        json={"path": "src/app.py", "content": "print('hi')\n", **common},
    )
    assert write.status_code == 200, write.text
    read = client.get(
        f"/workspaces/{pid}/file", params={"path": "src/app.py", **common}
    )
    assert read.status_code == 200
    assert read.json()["content"] == "print('hi')\n"
    assert read.json()["binary"] is False


def test_open_external_dedup_and_git(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    base = tmp_path / "projects"
    client = _client(monkeypatch, base)
    common = {"metadata_db_path": str(db_path)}

    external = tmp_path / "existing-repo"
    external.mkdir()
    (external / "main.py").write_text("x = 1\n", encoding="utf-8")

    opened = client.post("/workspaces/open", json={"path": str(external), **common})
    assert opened.status_code == 200, opened.text
    pid = opened.json()["id"]
    assert opened.json()["kind"] == "external"

    # Re-opening the same folder returns the same registration (no duplicate).
    again = client.post("/workspaces/open", json={"path": str(external), **common})
    assert again.status_code == 200
    assert again.json()["id"] == pid

    # Git endpoints always answer with a shape, even on a non-repo / no git.
    status = client.get(f"/workspaces/{pid}/git/status", params=common)
    assert status.status_code == 200
    assert "available" in status.json() and "files" in status.json()
    diff = client.get(f"/workspaces/{pid}/git/diff", params=common)
    assert diff.status_code == 200
    assert "diff" in diff.json()


def test_path_safety_rejects_escape(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    base = tmp_path / "projects"
    client = _client(monkeypatch, base)
    common = {"metadata_db_path": str(db_path)}

    external = tmp_path / "repo"
    external.mkdir()
    (external / "ok.txt").write_text("safe\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")

    pid = client.post(
        "/workspaces/open", json={"path": str(external), **common}
    ).json()["id"]

    # Traversal out of the project root must be refused (400), not served.
    escape = client.get(
        f"/workspaces/{pid}/file", params={"path": "../secret.txt", **common}
    )
    assert escape.status_code == 400
    assert "secret" not in escape.text.lower() or "verlässt" in escape.text.lower()

    # Writing outside the root is refused too.
    bad_write = client.put(
        f"/workspaces/{pid}/file",
        json={"path": "../escape.txt", "content": "nope", **common},
    )
    assert bad_write.status_code == 400
    assert not (tmp_path / "escape.txt").exists()


def test_file_and_dir_crud(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    base = tmp_path / "projects"
    client = _client(monkeypatch, base)
    common = {"metadata_db_path": str(db_path)}

    external = tmp_path / "repo"
    external.mkdir()
    pid = client.post(
        "/workspaces/open", json={"path": str(external), **common}
    ).json()["id"]

    assert client.post(
        f"/workspaces/{pid}/dir", json={"path": "pkg", **common}
    ).status_code == 200
    assert (external / "pkg").is_dir()

    assert client.post(
        f"/workspaces/{pid}/file", json={"path": "pkg/__init__.py", **common}
    ).status_code == 200
    assert (external / "pkg" / "__init__.py").exists()

    # Creating the same file again is a conflict.
    dup = client.post(
        f"/workspaces/{pid}/file", json={"path": "pkg/__init__.py", **common}
    )
    assert dup.status_code == 400

    deleted = client.request(
        "DELETE",
        f"/workspaces/{pid}/file",
        params={"path": "pkg", **common},
    )
    assert deleted.status_code == 200
    assert not (external / "pkg").exists()

    # Unregister leaves the folder on disk.
    unreg = client.request("DELETE", f"/workspaces/{pid}", params=common)
    assert unreg.status_code == 200
    assert unreg.json()["deleted"] is True
    assert external.is_dir()
