"""Sandbox ueber ``git worktree`` — eine isolierte Kopie auf einem Checkpoint.

Die Worktree-Kopie liegt unter ``data/sandboxes/<id>``, nie im Repo des
Nutzers. Der Testlauf ist argv-Liste ohne Shell, mit Timeout. Die Uebernahme
legt vorher einen Checkpoint an. Verwaiste Sandboxen werden aufgeraeumt.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from workspace import checkpoints, sandbox

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _git(
    root: Path, args: list[str], env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return proc.returncode, proc.stdout, proc.stderr


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    if _git(repo, ["init", "-q"])[0] != 0:
        pytest.skip("kein git auf diesem Rechner")
    (repo / "a.txt").write_text("alpha\n", encoding="utf-8")
    _git(repo, ["add", "a.txt"], env=_ENV)
    _git(repo, ["commit", "-q", "-m", "init"], env=_ENV)
    return repo


def test_create_worktree_on_a_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worktree entsteht unter data/sandboxes, nicht im Repo des Nutzers."""
    repo = _make_repo(tmp_path)
    # Sandboxen in ein Tempverzeichnis leiten, nicht ins echte data/.
    sandbox_dir = tmp_path / "sandboxes"
    monkeypatch.setattr(sandbox, "SANDBOXES_DIR", sandbox_dir)

    ckpt = checkpoints.create(repo, label="Stand A", reason="manual")
    assert ckpt.ref_name

    created = sandbox.create_worktree(repo, ckpt.commit_sha, "sb_test1")
    assert created["created"], created
    assert created["path"].startswith(str(sandbox_dir))
    # Der Worktree traegt den Stand des Checkpoints, nicht HEAD.
    wt = Path(created["path"])
    assert (wt / "a.txt").read_text(encoding="utf-8") == "alpha\n"


def test_run_tests_executes_a_trivial_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """argv-Liste, kein Shell — ein harmloser Befehl kommt sauber zurueck."""
    repo = _make_repo(tmp_path)
    sandbox_dir = tmp_path / "sandboxes"
    monkeypatch.setattr(sandbox, "SANDBOXES_DIR", sandbox_dir)
    ckpt = checkpoints.create(repo, label="Stand", reason="manual")
    created = sandbox.create_worktree(repo, ckpt.commit_sha, "sb_run")
    assert created["created"]

    result = sandbox.run_tests(
        Path(created["path"]), ["python", "-c", "import sys; sys.exit(0)"]
    )
    assert result.returncode == 0
    assert not result.timed_out


def test_apply_brings_sandbox_changes_back_to_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine in der Sandbox geaenderte Datei landet nach apply im Hauptbaum."""
    repo = _make_repo(tmp_path)
    sandbox_dir = tmp_path / "sandboxes"
    monkeypatch.setattr(sandbox, "SANDBOXES_DIR", sandbox_dir)
    ckpt = checkpoints.create(repo, label="Basis", reason="manual")
    created = sandbox.create_worktree(repo, ckpt.commit_sha, "sb_apply")
    assert created["created"]
    wt = Path(created["path"])

    # In der Sandbox aendern.
    (wt / "a.txt").write_text("beta\n", encoding="utf-8")
    (wt / "new.txt").write_text("neu\n", encoding="utf-8")

    result = sandbox.apply_to_main(repo, wt, ckpt.commit_sha, author_env=_ENV)
    assert result["applied"], result
    paths = {entry["path"] for entry in result["entries"]}
    assert "a.txt" in paths
    assert "new.txt" in paths
    # Hauptbaum uebernommen.
    assert (repo / "a.txt").read_text(encoding="utf-8") == "beta\n"
    assert (repo / "new.txt").read_text(encoding="utf-8") == "neu\n"


def test_remove_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """remove_worktree raeumt Verzeichnis und Registrierung."""
    repo = _make_repo(tmp_path)
    sandbox_dir = tmp_path / "sandboxes"
    monkeypatch.setattr(sandbox, "SANDBOXES_DIR", sandbox_dir)
    ckpt = checkpoints.create(repo, label="Basis", reason="manual")
    created = sandbox.create_worktree(repo, ckpt.commit_sha, "sb_rm")
    assert created["created"]
    wt = Path(created["path"])
    assert wt.is_dir()

    out = sandbox.remove_worktree(repo, "sb_rm")
    assert out["removed"]
    assert not wt.exists()
    # ``git worktree list`` fuehrt den entfernten nicht mehr.
    listing = _git(repo, ["worktree", "list"])[1]
    assert str(wt) not in listing


def test_detect_test_command_picks_pytest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    cmd = sandbox.detect_test_command(repo)
    assert cmd is not None
    assert "pytest" in " ".join(cmd)
