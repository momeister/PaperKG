"""Refactor-Vorschlag prüfen, ohne ihn zu glauben.

Der Test, der zählt, ist nicht, ob der Vorschlag klug ist — das hängt vom Modell
ab und ist nicht deterministisch. Der Test, der zählt, ist, dass ein Vorschlag,
der das Projekt verlässt oder syntaktisch kaputt ist, **abgelehnt** wird, bevor
irgendetwas geschrieben wird. Genau das ist die Sicherheitslinie zwischen einem
Modelltext und einer geschriebenen Datei.
"""

from __future__ import annotations

from pathlib import Path

from codegraph import refactor


def _proposal(*files: tuple[str, str], geloescht=None) -> dict:
    return {
        "begruendung": "kleinerer Test",
        "dateien": [{"pfad": p, "inhalt": c} for p, c in files],
        "geloescht": geloescht or [],
    }


def test_a_path_that_leaves_the_project_is_rejected(tmp_path: Path) -> None:
    """``../../etc/passwd`` fällt an der Pfadprüfung, nicht erst am Testlauf."""
    ok, errors, _cleaned = refactor.validate_proposal(
        tmp_path, _proposal(("../etc/passwd", "x"))
    )
    assert not ok
    assert any("verlässt" in e or "passwd" in e for e in errors)


def test_a_syntactically_broken_python_file_is_rejected(tmp_path: Path) -> None:
    """Ein kaputter Parse fällt am ``compile``-Check, bevor der Worktree berührt wird."""
    broken = "def f(:\n    pass\n"
    ok, errors, _cleaned = refactor.validate_proposal(
        tmp_path, _proposal(("mod.py", broken))
    )
    assert not ok
    assert any("Syntaxfehler" in e for e in errors)


def test_a_valid_python_proposal_passs(tmp_path: Path) -> None:
    ok, errors, cleaned = refactor.validate_proposal(
        tmp_path, _proposal(("mod.py", "def f():\n    return 1\n"))
    )
    assert ok, errors
    assert [f["pfad"] for f in cleaned["dateien"]] == ["mod.py"]


def test_an_empty_proposal_is_no_proposal(tmp_path: Path) -> None:
    ok, errors, _cleaned = refactor.validate_proposal(tmp_path, {"begruendung": "nix"})
    assert not ok
    assert any("keine Änderungen" in e for e in errors)


def test_a_non_python_file_skips_the_compile_check(tmp_path: Path) -> None:
    """Nicht-Python wird hier nicht geprüft — der Reindex in der Sandbox entscheidet."""
    ok, errors, _cleaned = refactor.validate_proposal(
        tmp_path, _proposal(("data.json", "{ not valid json"))
    )
    assert ok, errors
