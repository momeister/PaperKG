"""Git-Checkpoints fuer die Werkstatt — der alternative Index darf den
Staging-Bereich des Nutzers niemals anfassen.

Der wichtigste Test steht zuerst: wer eine Datei per ``git add`` gestagt hat,
muss nach einem Checkpoint immer noch genau diese Datei gestagt sehen. Ein
``git add`` ohne umgeleiteten Index wuerde das zerstoeren — das ist der Fehler,
den das Modul verhindert.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from workspace import checkpoints

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


def test_a_staged_file_stays_staged_after_a_checkpoint(tmp_path: Path) -> None:
    """Der Index des Nutzers ist das, was ein falscher Checkpoint zerstoeren wuerde."""
    repo = _make_repo(tmp_path)
    # Eine *zweite* Datei explizit stage — sie darf nach dem Checkpoint noch
    # genau so dastehen (gestagt, nicht committet, nicht weg).
    (repo / "b.txt").write_text("beta\n", encoding="utf-8")
    _git(repo, ["add", "b.txt"], env=_ENV)
    _gestagt_vorher = _git(repo, ["diff", "--cached", "--name-only"])[1]

    result = checkpoints.create(repo, label="vor Änderung", reason="manual")

    assert result.ref_name, f"Checkpoint fehlgeschlagen: {result.error}"
    _gestagt_nachher = _git(repo, ["diff", "--cached", "--name-only"])[1]
    assert _gestagt_nachher == _gestagt_vorher, (
        "Der Staging-Bereich des Nutzers wurde veraendert — genau das darf ein "
        "Checkpoint nicht tun."
    )
    # HEAD ebenfalls unangetastet: der Commit-Zaehler darf gleich bleiben.
    _anzahl_commits_vorher = _git(repo, ["rev-list", "--count", "HEAD"])[1].strip()
    assert _anzahl_commits_vorher == "1"
    _anzahl_commits_nachher = _git(repo, ["rev-list", "--count", "HEAD"])[1].strip()
    assert _anzahl_commits_nachher == "1"


def test_untracked_file_is_captured_ignored_is_not(tmp_path: Path) -> None:
    """Untracked kommt mit, .gitignore bleibt draussen — beides ist gewollt."""
    repo = _make_repo(tmp_path)
    (repo / "neu.txt").write_text("neu\n", encoding="utf-8")
    (repo / ".gitignore").write_text("secret.log\n", encoding="utf-8")
    _git(repo, ["add", ".gitignore"], env=_ENV)
    _git(repo, ["commit", "-q", "-m", "ignore"], env=_ENV)
    (repo / "secret.log").write_text("geheim\n", encoding="utf-8")

    result = checkpoints.create(repo, label="mit untracked", reason="manual")
    assert result.ref_name
    dateien = _git(repo, ["ls-tree", "-r", "--name-only", result.commit_sha])[1].split()
    assert "neu.txt" in dateien
    assert ".gitignore" in dateien
    assert (
        "secret.log" not in dateien
    ), "Eine ignorierte Datei darf nicht gesichert werden."


def test_repo_without_commits_works(tmp_path: Path) -> None:
    """Ein frisches Repo ohne Commits hat keinen HEAD — commit-tree ohne -p."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if _git(repo, ["init", "-q"])[0] != 0:
        pytest.skip("kein git auf diesem Rechner")
    (repo / "x.txt").write_text("x\n", encoding="utf-8")

    result = checkpoints.create(repo, label="erster", reason="manual", author_env=_ENV)
    assert result.ref_name, f"Checkpoint ohne HEAD fehlgeschlagen: {result.error}"
    assert result.parent_sha is None
    # Der Checkpoint ist ein gueltiger, elternloser Commit.
    assert _git(repo, ["cat-file", "-t", result.commit_sha])[1].strip() == "commit"


def test_restore_brings_back_deleted_and_removes_new_file(tmp_path: Path) -> None:
    """Rücksprung: geloeschte Datei kehrt zurueck, neu entstandene verschwindet."""
    repo = _make_repo(tmp_path)
    # Eine dritte Datei, die im Checkpoint existiert, danach geloescht wird.
    (repo / "c.txt").write_text("gamma\n", encoding="utf-8")
    _git(repo, ["add", "c.txt"], env=_ENV)
    _git(repo, ["commit", "-q", "-m", "c"], env=_ENV)
    result = checkpoints.create(repo, label="Stand A", reason="manual")
    assert result.ref_name

    # Stand A → jetzt: c.txt loeschen, d.txt neu anlegen.
    (repo / "c.txt").unlink()
    (repo / "d.txt").write_text("delta\n", encoding="utf-8")

    plan = checkpoints.restore_plan(repo, result.commit_sha)
    wege = {entry["path"]: entry["status"] for entry in plan["entries"]}
    assert wege.get("c.txt") == "D"
    assert wege.get("d.txt") == "A"

    outcome = checkpoints.restore(
        repo, result.commit_sha, plan["plan_hash"], author_env=_ENV
    )
    assert outcome["applied"], outcome
    # c.txt zurueck, d.txt weg.
    assert (repo / "c.txt").read_text(encoding="utf-8") == "gamma\n"
    assert not (repo / "d.txt").exists()
    # Die Sicherung vor dem Rücksprung ist selbst ein Checkpoint.
    assert outcome.get("backup_ref")


def test_restore_refuses_a_stale_plan(tmp_path: Path) -> None:
    """Aendert sich der Baum zwischen Vorschau und Apply → 409-Bild (stale)."""
    repo = _make_repo(tmp_path)
    result = checkpoints.create(repo, label="Stand", reason="manual")
    assert result.ref_name
    plan = checkpoints.restore_plan(repo, result.commit_sha)
    # Aendere den Baum *nach* der Vorschau.
    (repo / "d.txt").write_text("delta\n", encoding="utf-8")
    outcome = checkpoints.restore(
        repo, result.commit_sha, plan["plan_hash"], author_env=_ENV
    )
    assert not outcome["applied"]
    assert outcome["reason"] == "stale"


def test_no_git_is_fail_soft_not_an_exception(tmp_path: Path) -> None:
    """Ohne git/Repo: Grund statt Text, niemals eine Exception."""
    repo = tmp_path / "kein-git"
    repo.mkdir()
    # git_available prueft shutil.which('git'); ist git installiert, aber kein
    # Repo, muss ``no_repo`` rauskommen.
    if not checkpoints.git_available():
        result = checkpoints.create(repo, label="x", reason="manual")
        assert result.reason == "no_git"
        return
    result = checkpoints.create(repo, label="x", reason="manual")
    assert result.reason == "no_repo"
