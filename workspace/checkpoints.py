"""Git-Checkpoints fuer die Werkstatt — ein Weg zurueck, ohne den Staging-
Bereich des Nutzers anzufassen.

Ein Checkpoint erfasst den **ganzen** Arbeitsbaum (inkl. untracked, exkl.
gitignore) als eigenen Commit unter ``refs/paperkg/checkpoints/<id>``. Weder
HEAD noch der Index des Nutzers werden beruehrt — das ist der Punkt: ein
``git add`` ohne umgeleiteten Index wuerde den Staging-Bereich des Nutzers
zerstoeren, und genau das darf nicht passieren.

Technisch: ein alternativer Index ueber ``GIT_INDEX_FILE=<tmp>``, ``git add -A``,
``git write-tree``, ``git commit-tree [-p HEAD]``, ``git update-ref``.

Randfaelle, die hier stehen muessen:

* **Repo ohne Commits** (kein HEAD): ``commit-tree`` ohne ``-p``. Ein
  elternloser Commit ist gueltig und fuer ``git worktree add`` brauchbar.
* **.gitignore** wird respektiert — ``node_modules``/``.venv`` bleiben draussen.
  Preis: eine ignorierte, aber wichtige Datei ist *nicht* gesichert. Das ist
  eine bewusste Entscheidung (sonst waere ein Checkpoint gigabyteschwer) und
  wird in der UI gesagt, nicht im Kleingedruckten.
* Kein git / kein Repo: ``reason`` statt Text — git ist lokalisiert.

Die Modulebene ist absichtlich eine reine Funktionssammlung (wie
``workspace.manager``), damit Tests ``base_dir``/Tempordner monkeypatchen
koennen und der Router es als Modul importiert.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workspace import manager as workspace_manager
from workspace.manager import (
    _run_git,
    git_available,
    is_git_repo,
)  # noqa: PLC2701 — privater Helfer, bewusst geteilt

#: Eigene Ref-Namespace. Refs unter ``refs/paperkg/`` schuetzen ihre Objekte vor
#: ``git gc`` — genau deshalb liegen sie dort, nicht bei ``refs/heads/``.
REF_NAMESPACE = "refs/paperkg/checkpoints"

#: Hoechstens so viele *automatische* Checkpoints pro Projekt behalten. Manuelle
#: werden nie automatisch geloescht. 50 ist eine Messung, kein Geschmack: bei
#: einem Checkpoint vor jeder Symbol-Schreibung sind das ~50 Ruckgaenge, was
#: selbst an einem langen Arbeitstag reicht; mehr muesste niemand durchsuchen.
MAX_AUTO_KEPT = 50


@dataclass
class CheckpointResult:
    """Was :func:`create` geliefert hat — ein Grund steht immer dabei."""

    ref_name: str | None
    commit_sha: str | None
    tree_sha: str | None
    parent_sha: str | None
    file_count: int
    reason: str  # manual|auto_*|no_git|no_repo|error
    error: str | None = None
    label: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref_name": self.ref_name,
            "commit_sha": self.commit_sha,
            "tree_sha": self.tree_sha,
            "parent_sha": self.parent_sha,
            "file_count": self.file_count,
            "reason": self.reason,
            "error": self.error,
            "label": self.label,
        }


def _has_head(root: Path) -> str | None:
    """SHA von HEAD, oder ``None`` wenn das Repo (noch) keine Commits hat."""
    code, out, _ = _run_git(root, ["rev-parse", "--verify", "-q", "HEAD"])
    if code != 0:
        return None
    sha = out.strip()
    return sha or None


def _count_tree_entries(root: Path, tree_sha: str) -> int:
    """Anzahl Dateien in einem Baum (rekursiv) — fuer die Vorschau/Dauer-Schaetzung."""
    code, out, _ = _run_git(root, ["ls-tree", "-r", "--name-only", tree_sha])
    if code != 0:
        return 0
    return sum(1 for line in out.splitlines() if line.strip())


def create(
    root: Path,
    *,
    label: str,
    reason: str,
    author_env: dict[str, str] | None = None,
) -> CheckpointResult:
    """Einen Checkpoint anlegen — beruehrt HEAD/Index/Arbeitsbaum nicht.

    ``reason`` ist die automatisierte Buchfuehrung (``manual``,
    ``auto_symbol_write`` …). ``label`` steht im Commit-Betreff und spaeter in
    der UI; ein praegnanter Satz, kein Pfad.
    """
    if not git_available():
        return CheckpointResult(None, None, None, None, 0, "no_git")
    if not is_git_repo(root):
        return CheckpointResult(None, None, None, None, 0, "no_repo")

    # Eigener Index im Tempverzeichnis — *nicht* im ``.git`` des Nutzers, damit
    # kein Halbzeug liegen bleibt, wenn der Prozess abstirbt. ``mkstemp`` legt
    # eine leere Datei an; git lehnt eine 0-Byte-Indexdatei aber ab
    # („Index-Datei ist kleiner als erwartet"). Also Pfad merken, Datei loeschen
    # — git add legt einen frischen Index an.
    index_fd, index_path = tempfile.mkstemp(prefix="paperkg_idx_", suffix=".tmp")
    os.close(index_fd)
    os.unlink(index_path)
    index_env = {"GIT_INDEX_FILE": index_path}
    commit_env = {
        **(
            author_env
            or {
                "GIT_AUTHOR_NAME": "PaperKG",
                "GIT_AUTHOR_EMAIL": "werkstatt@paperkg.local",
                "GIT_COMMITTER_NAME": "PaperKG",
                "GIT_COMMITTER_EMAIL": "werkstatt@paperkg.local",
            }
        ),
        **index_env,
    }
    try:
        # ``add -A`` mit umgeleitetem Index: erfasst tracked + untracked, respektiert
        # .gitignore, beruehrt den echten Index nicht. Backslashes (Windows) nimmt
        # git selbst; Forward-Slashes sind im Pfad sowieso Standard.
        code, _out, err = _run_git(root, ["add", "-A"], env=index_env)
        if code != 0:
            return CheckpointResult(None, None, None, None, 0, "error", err.strip())

        code, out, err = _run_git(root, ["write-tree"], env=index_env)
        if code != 0 or not out.strip():
            return CheckpointResult(None, None, None, None, 0, "error", err.strip())
        tree_sha = out.strip()

        parent = _has_head(root)
        commit_args = ["commit-tree", tree_sha, "-m", label]
        if parent:
            commit_args[1:1] = ["-p", parent]
        code, out, err = _run_git(root, commit_args, env=commit_env)
        if code != 0 or not out.strip():
            return CheckpointResult(None, None, None, None, 0, "error", err.strip())
        commit_sha = out.strip()

        ref_name = f"{REF_NAMESPACE}/{commit_sha[:16]}"
        code, _out, err = _run_git(root, ["update-ref", ref_name, commit_sha])
        if code != 0:
            return CheckpointResult(
                None, commit_sha, tree_sha, parent, 0, "error", err.strip()
            )

        file_count = _count_tree_entries(root, tree_sha)
        return CheckpointResult(
            ref_name, commit_sha, tree_sha, parent, file_count, reason, label=label
        )
    finally:
        try:
            os.unlink(index_path)
        except OSError:
            pass


def _parse_name_status(out: str) -> list[dict[str, str]]:
    """``git diff-tree -r --name-status`` Ausgabe → [{status, path}]."""
    entries: list[dict[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, path = parts
        # R/COPY/Unmerge schreiben den Quellpfad hinter einen Pfeil; den braucht
        # der Rücksprung nicht, nur den Zielpfad.
        path = path.split("\t")[-1]
        entries.append({"status": status[0], "path": path})
    return entries


def diff(root: Path, commit_sha: str) -> dict[str, Any]:
    """Was unterscheidet den Checkpoint vom aktuellen Arbeitsbaum?

    Richtung Checkpoint -> jetzt: ``A`` = seither entstanden (beim Rücksprung
    zu loeschen), ``D`` = seither verschwunden (zurueckzuholen), ``M`` = geaendert.
    """
    if not git_available() or not is_git_repo(root):
        return {"available": False, "entries": []}
    # Aktueller Stand als Baum (wieder ueber eigenen Index, ohne echte Stage).
    index_fd, index_path = tempfile.mkstemp(prefix="paperkg_diff_", suffix=".tmp")
    os.close(index_fd)
    os.unlink(index_path)
    index_env = {"GIT_INDEX_FILE": index_path}
    try:
        _run_git(root, ["add", "-A"], env=index_env)
        code, out, _ = _run_git(root, ["write-tree"], env=index_env)
        if code != 0 or not out.strip():
            return {"available": True, "entries": [], "error": "write-tree"}
        now_tree = out.strip()
        code, out, _ = _run_git(
            root, ["diff-tree", "-r", "--name-status", commit_sha, now_tree]
        )
        if code != 0:
            return {"available": True, "entries": [], "error": "diff-tree"}
        return {"available": True, "entries": _parse_name_status(out)}
    finally:
        try:
            os.unlink(index_path)
        except OSError:
            pass


def restore_plan(root: Path, commit_sha: str) -> dict[str, Any]:
    """Vorschau des Rücksprungs: Pfade + Aktion + ein ``plan_hash``.

    Der ``plan_hash`` sichert die Vorschau gegen Aenderungen zwischen Vorschau
    und Apply (409, wenn sich etwas geaendert hat) — analog der ``content_hash``-
    Sperre beim Symbol-Schreiben.
    """
    state = diff(root, commit_sha)
    entries = state.get("entries") or []
    h = hashlib.sha256()
    for entry in entries:
        h.update(entry["status"].encode("utf-8"))
        h.update(b"\x1f")
        h.update(entry["path"].encode("utf-8"))
        h.update(b"\x1e")
    # Der Plan muss auch unguelig werden, wenn sich der *Ziel*-Checkpoint aendert
    # (zweites Fenster stellt zwischenzeitlich einen neuen Checkpoint her).
    h.update(commit_sha.encode("utf-8"))
    plan_hash = h.hexdigest()[:16]
    return {
        "available": state.get("available", False),
        "entries": entries,
        "plan_hash": plan_hash,
        "commit_sha": commit_sha,
    }


def restore(
    root: Path,
    commit_sha: str,
    plan_hash: str,
    *,
    label: str = "vor Rücksprung",
    author_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Den Rücksprung ausfuehren — erst sichern, dann pfadweise zuruecksetzen.

    Kein ``git checkout .``: das wuerde auch ignorierte Dateien anfassen und den
    Arbeitsbaum in einem unbekannten Zustand hinterlassen. Stattdessen:
    geaenderte/geloeschte Pfade aus dem Checkpoint zurueckholen, neu entstandene
    loeschen — beides ueber ``resolve_within`` (Pfad bleibt im Projekt).
    """
    plan = restore_plan(root, commit_sha)
    if not plan.get("available"):
        return {"applied": False, "reason": plan.get("reason", "no_git")}
    if plan.get("plan_hash") != plan_hash:
        return {
            "applied": False,
            "reason": "stale",
            "expected": plan_hash,
            "got": plan_hash,
        }

    # Vor dem Rücksprung selbst sichern — sonst ist der aktuelle Stand weg, wenn
    # man den Rücksprung bereut.
    sicherung = create(root, label=label, reason="pre_restore", author_env=author_env)

    entries = plan.get("entries") or []
    applied: list[dict[str, str]] = []
    for entry in entries:
        status = entry["status"]
        rel = entry["path"]
        try:
            target = workspace_manager.resolve_within(root, rel)
        except Exception:
            applied.append(
                {"path": rel, "status": status, "action": "skipped", "reason": "escape"}
            )
            continue
        if status == "A":
            # Seither entstanden → loeschen.
            if target.is_file() or target.is_symlink():
                target.unlink()
                applied.append({"path": rel, "status": status, "action": "deleted"})
            elif target.is_dir():
                # Ein neuer Ordner kann nicht aus einem einzelnen Pfad entstehen;
                # ``git diff-tree`` listet Dateien, also ist das ein seltener Fall.
                import shutil

                shutil.rmtree(target, ignore_errors=True)
                applied.append({"path": rel, "status": status, "action": "deleted"})
            else:
                applied.append(
                    {
                        "path": rel,
                        "status": status,
                        "action": "skipped",
                        "reason": "gone",
                    }
                )
        else:
            # M oder D → aus dem Checkpoint zurueckholen. ``git restore --source``
            # beruehrt nur den Arbeitsbaum, nicht die Stage (git >= 2.23).
            code, _out, err = _run_git(
                root, ["restore", "--source", commit_sha, "--worktree", "--", rel]
            )
            if code != 0:
                # Aelteres git: ``checkout <sha> -- <pfad>`` ist das gleiche Bild.
                code, _out, err = _run_git(root, ["checkout", commit_sha, "--", rel])
            if code == 0:
                applied.append({"path": rel, "status": status, "action": "restored"})
            else:
                applied.append(
                    {
                        "path": rel,
                        "status": status,
                        "action": "failed",
                        "reason": err.strip(),
                    }
                )

    return {
        "applied": True,
        "commit_sha": commit_sha,
        "backup_ref": sicherung.ref_name,
        "backup_sha": sicherung.commit_sha,
        "entries": applied,
    }


def drop(root: Path, ref_name: str) -> bool:
    """Ref loeschen — die Objekte werden spaetestens beim naechsten ``git gc``
    mitgerissen, sobald keine Ref mehr darauf zeigt."""
    if not git_available() or not is_git_repo(root):
        return False
    code, _out, _err = _run_git(root, ["update-ref", "-d", ref_name])
    return code == 0


def auto_checkpoint(
    db: Any,
    code_project_id: str,
    root: Path,
    *,
    reason: str,
    label: str,
) -> dict[str, Any]:
    """Vor einem Schreiben sichern — fail-soft, blockiert niemals.

    Das ist die gemeinsame Naht fuer Symbol-PATCH, Datei-Speichern,
    Refactor-Anwenden und Sandbox-Uebernahme. Ein Fehlschlag (kein git, kein
    Repo, ``commit-tree`` scheitert) wird *nicht* weitergereicht — der Nutzer
    muss in einem Nicht-git-Ordner weiterarbeiten koennen. Die Antwort traegt
    ``checkpoint: null`` und ``reason``, damit die UI es einmal sagen kann.

    Danach werden zu viele automatische Checkpoints aufgeraeumt: die neuesten
    ``MAX_AUTO_KEPT`` bleiben, aeltere automatisch angelegte werden geloescht
    (Ref + DB-Zeile). Manuelle werden nie angeruehrt.
    """
    result = create(root, label=label, reason=reason)
    if not result.ref_name:
        return {"checkpoint": None, "reason": result.reason, "error": result.error}
    record = db.add_code_checkpoint(
        code_project_id,
        ref_name=result.ref_name,
        commit_sha=result.commit_sha,
        tree_sha=result.tree_sha,
        parent_sha=result.parent_sha,
        label=result.label,
        reason=reason,
        file_count=result.file_count,
    )
    _prune_auto(db, code_project_id, root)
    return {"checkpoint": record, "reason": reason}


def _prune_auto(db: Any, code_project_id: str, root: Path) -> None:
    """Aeltere *automatische* Checkpoints jenseits ``MAX_AUTO_KEPT`` loeschen."""
    # Sammle alle automatischen Gruende (auto_*). Manuelle bleiben unangetastet.
    kept: list[dict[str, Any]] = []
    for auto_reason in (
        "auto_symbol_write",
        "auto_file_write",
        "auto_refactor",
        "auto_sandbox_apply",
    ):
        kept.extend(db.list_code_checkpoints_by_reason(code_project_id, auto_reason))
    # Neueste zuerst (DB liefert aelteste zuerst bei ASC). Sortieren nach
    # created_timestamp absteigend.
    kept.sort(key=lambda r: str(r.get("created_timestamp") or ""), reverse=True)
    for stale in kept[MAX_AUTO_KEPT:]:
        ref_name = stale.get("ref_name")
        cid = stale.get("id")
        if ref_name:
            drop(root, ref_name)
        if cid:
            db.delete_code_checkpoint(cid)
