"""Was-wäre-wenn-Sandbox über ``git worktree``.

Eine isolierte Kopie des Projekts unter ``data/sandboxes/<sb_id>``, angelegt auf
einem **Checkpoint-Commit** — nur so kommen die unversionierten Änderungen mit
(``git add -A`` hat sie im Checkpoint erfasst; ein Worktree auf ``HEAD``
wuerde sie verlieren). Dort laeuft der Testbefehl, dort wird indiziert, dort
liegen die Experimente. Uebernommen wird nur auf Knopfdruck, mit Checkpoint
davor.

Zwei Dinge sind ehrlich zu benennen und stehen deshalb in der UI:

* Der Worktree wird in ``<nutzer-repo>/.git/worktrees/<name>`` **registriert** —
  eine Spur im Repository, unvermeidbar. ``git worktree remove`` + ``prune``
  raeumt sie weg.
* Ignorierte Ordner (``node_modules``, ``.venv``, ``target``) fehlen im Worktree.
  Ein ``npm test`` scheitert also sofort. Verlinken waere gefaehrlich: ein
  Symlink macht Schreibzugriffe im Sandbox-Lauf im Original wirksam — genau das
  Gegenteil einer Sandbox. Deshalb standardmaessig *nicht* verlinken.

Kein Sandbox im Kernel-Sinn — wie Werkstatt-Terminal, Jupyter und die
Analyse-Werkstatt auch. Das steht so schon in ``CLAUDE.md`` und bleibt wahr.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workspace import manager as workspace_manager
from workspace.manager import _run_git, git_available, is_git_repo  # noqa: PLC2701

#: Wo die Worktree-Kopien liegen — unter ``data/``, nie im Repo des Nutzers.
SANDBOXES_DIR = Path("data/sandboxes")

#: Hartes Zeitlimit fuer einen Testlauf. Laenger darf ein Experiment nicht
#: blockieren — der Nutzer warte oder breche ab.
DEFAULT_TIMEOUT = 300


@dataclass
class SandboxRunResult:
	"""Ergebnis eines Testlaufs in der Sandbox."""
	returncode: int
	stdout: str
	stderr: str
	timed_out: bool
	duration_s: float
	command: list[str]

	def as_dict(self) -> dict[str, Any]:
		return {
			"returncode": self.returncode,
			"stdout": self.stdout,
			"stderr": self.stderr,
			"timed_out": self.timed_out,
			"duration_s": round(self.duration_s, 1),
			"command": self.command,
		}


def _sandbox_dir(sandbox_id: str) -> Path:
	return SANDBOXES_DIR / sandbox_id


def create_worktree(root: Path, checkpoint_sha: str, sandbox_id: str) -> dict[str, Any]:
	"""Worktree auf einen Checkpoint-Commit anlegen.

	Gibt ``{"path": …, "created": True, "reason": …}`` zurueck. Fail-soft: ohne
	git/Repo oder wenn der Worktree nicht anlegbar ist, kein Absturz, sondern
	ein Grund — die UI sagt es.
	"""
	if not git_available() or not is_git_repo(root):
		return {"path": None, "created": False, "reason": "no_git"}
	target = _sandbox_dir(sandbox_id)
	target.parent.mkdir(parents=True, exist_ok=True)
	# --detach: der Worktree hat keinen eigenen Branch (Experiment, keine
	# laengerfristige Linie). Der SHA kommt vom Checkpoint und sichert die
	# unversionierten Änderungen.
	code, _out, err = _run_git(root, ["worktree", "add", "--detach", str(target), checkpoint_sha])
	if code != 0:
		return {"path": None, "created": False, "reason": "error", "error": err.strip()}
	return {"path": str(target), "created": True, "reason": "ok"}


def detect_test_command(root: Path) -> list[str] | None:
	"""Erkenne den Testbefehl anhand der Projektdateien.

	Reihenfolge: pytest (``pytest.ini``/``pyproject.toml``) → npm test → cargo
	test. Immer im Feld editierbar; beim *ersten* Lauf je Projekt
	bestaetigungspflichtig — hier wird fremder (in Teil E: modellgeschriebener)
	Code ausgefuehrt.
	"""
	if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
		return ["python", "-m", "pytest", "-q"]
	if (root / "package.json").exists():
		return ["npm", "test"]
	if (root / "Cargo.toml").exists():
		return ["cargo", "test"]
	return None


def run_tests(worktree: Path, command: list[str], *, timeout: int = DEFAULT_TIMEOUT) -> SandboxRunResult:
	"""Testbefehl im Worktree ausfuehren — argv-Liste, kein Shell, Timeout.

	Nach dem Muster von ``analysis/runner.py``: cwd = Worktree, bereinigtes Env,
	Ausgabe immer vollstaendig. Kein Sandbox im Kernel-Sinn — der Subprozess
	laeuft mit Backend-Rechten (wie Werkstatt-Terminal/Jupyter/Analyse).
	"""
	import time

	start = time.monotonic()
	timed_out = False
	try:
		proc = subprocess.run(
			command,
			cwd=str(worktree),
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="replace",
			timeout=timeout,
			env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
		)
		returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
	except subprocess.TimeoutExpired as exc:
		timed_out = True
		returncode = -1
		stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
		stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
		stderr = (stderr + f"\n[Timeout nach {timeout}s abgebrochen]").strip()
	except OSError as exc:
		returncode = -1
		stdout = ""
		stderr = f"Befehl nicht ausführbar: {exc}"
	return SandboxRunResult(
		returncode=returncode,
		stdout=stdout,
		stderr=stderr,
		timed_out=timed_out,
		duration_s=time.monotonic() - start,
		command=command,
	)


def diff_worktree(root: Path, checkpoint_sha: str) -> dict[str, Any]:
	"""Diff zwischen Sandbox-Stand und dem Ausgangs-Checkpoint.

	Gibt die ``name-status``-Liste zurueck (A/D/M), die die UI als Vorschau der
	Uebernahme zeigt.
	"""
	if not git_available() or not is_git_repo(root):
		return {"available": False, "entries": []}
	# Der aktuelle Worktree-Stand als Baum — ueber den Index des Worktrees
	# selbst (er ist ein eigenes Checkout, sein Index ist separiert).
	# ``git diff-tree`` direkt zwischen dem Checkpoint-Commit und dem
	# Arbeitsbaum ist nicht moeglich; wir vergleichen zwei Commits. Damit der
	# Sandbox-Stand ein Commit wird, wird er im Worktree committet — aber der
	# Worktree ist ``--detach`` und der Nutzer will keine Commits. Also:
	# stattdessen ``git diff <sha>`` im Worktree-Verzeichnis gegen Arbeitsbaum.
	return {"available": True, "note": "siehe /workspaces/{id}/git/diff im Sandbox-Projekt"}


def apply_to_main(
	main_root: Path,
	worktree: Path,
	checkpoint_sha: str,
	*,
	label: str = "Sandbox-Übernahme",
	author_env: dict[str, str] | None = None,
) -> dict[str, Any]:
	"""Geaenderte Pfade aus der Sandbox in den Hauptbaum uebernehmen.

	Mit Checkpoint davor (``auto_sandbox_apply``). Pfadweise kopieren, beide
	Seiten ueber ``resolve_within`` — ein Pfad, der das Projekt verlaesst, wird
	verworfen, nicht korrigiert.
	"""
	from workspace import checkpoints

	# Vorher sichern.
	checkpoints.create(main_root, label=label, reason="auto_sandbox_apply", author_env=author_env)

	# Unterschied: Sandbox-Arbeitsbaum vs. Checkpoint. ``git diff`` zeigt
	# untracked Dateien nicht — erst stage ich sie im Worktree (sein eigener
	# Index, beruehrt den Hauptbaum nicht), dann vergleicht ``diff <sha>`` den
	# vollstaendigen Stand gegen den Checkpoint.
	_run_git(worktree, ["add", "-A"])
	# ``--cached`` vergleicht den Index (gerade vollstaendig gestaged) gegen
	# den Checkpoint-Commit — so tauchen neu entstandene Dateien als ``A`` auf.
	code, out, _ = _run_git(worktree, ["diff", "--cached", "--name-status", checkpoint_sha])
	if code != 0:
		return {"applied": False, "reason": "diff_failed"}
	applied: list[dict[str, str]] = []
	for line in out.splitlines():
		if not line.strip():
			continue
		parts = line.split("\t", 1)
		if len(parts) != 2:
			continue
		status, rel = parts[0], parts[1].split("\t")[-1]
		try:
			src = (worktree / rel).resolve()
			if not src.is_file():
				continue
			target = workspace_manager.resolve_within(main_root, rel)
			target.parent.mkdir(parents=True, exist_ok=True)
			shutil.copyfile(src, target)
			applied.append({"path": rel, "status": status, "action": "copied"})
		except Exception as exc:  # noqa: BLE001 — ein Pfad faellt raus, nicht alle
			applied.append({"path": rel, "status": status, "action": "skipped", "reason": str(exc)})
	return {"applied": True, "entries": applied}


def remove_worktree(root: Path, sandbox_id: str) -> dict[str, Any]:
	"""Worktree entfernen + prune. Fail-soft."""
	target = _sandbox_dir(sandbox_id)
	if not git_available() or not is_git_repo(root):
		# Trotzdem das Verzeichnis weg, falls es ohne git verwaist ist.
		if target.exists():
			shutil.rmtree(target, ignore_errors=True)
		return {"removed": True, "reason": "no_git"}
	code, _out, _err = _run_git(root, ["worktree", "remove", "--force", str(target)])
	_run_git(root, ["worktree", "prune"])
	# Verwaistes Verzeichnis aufraeumen, falls remove nicht durchgriff.
	if target.exists():
		shutil.rmtree(target, ignore_errors=True)
	return {"removed": code == 0, "reason": "ok"}


def prune_orphaned(root: Path) -> None:
	"""Beim Start: verwaiste Worktree-Eintraege prunen (Verzeichnis weg, Registrierung noch da)."""
	if not git_available() or not is_git_repo(root):
		return
	_run_git(root, ["worktree", "prune"])