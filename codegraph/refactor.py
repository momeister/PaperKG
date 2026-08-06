"""Spaghetti-Löser — Diagnose ohne Modell, Vorschlag mit Modell, geprüft statt geglaubt.

Zwei Hälften, die nicht vermischt werden:

* **Diagnose** (``hotspots`` + ``cycles``) liegt in Rust und kommt ohne LLM
  aus. Jede Fundstelle trägt, *welche* Regel sie gerissen hat und mit welchem
  Messwert — das Belegprinzip auf Zahlen übertragen. Das ist die Hälfte, die
  auch ohne Modell nützlich ist, und sie steht im Knäuel-Tab immer da.
* **Vorschlag** (``propose_stream``) ist eine LLM-Antwort und wird als solche
  behandelt: nicht geglaubt, sondern geprüft. Vier Prüfungen, bevor irgendetwas
  geschrieben wird — siehe :func:`validate_proposal`.

Das Ausgabeformat ist **vollständiger neuer Inhalt je Datei**, keine
Zeilenoperationen. Zeilenoperationen driften, sobald sich das Modell um eine
Zeile vertut, und ``_splice_lines`` deckt nur ein einzelnes Symbol ab. Ein
ganzer Dateiinhalt ist either korrekt oder offensichtlich kaputt, und der
Testlauf in der Sandbox (Teil D) entscheidet endgültig.

Ohne Modell ist der Tab trotzdem nützlich: die Diagnose steht, der
Vorschlagsknopf zeigt den üblichen Hinweis (wie überall sonst im Code-Graph).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from codegraph import service
from codegraph.binary import CodeSearchMissingError
from codegraph.llm_prefs import model_overrides
from codegraph.rpc import CodeGraphError
from query.llm_router import LLMRouter
from workspace import manager as workspace_manager
from workspace.manager import WorkspaceError

#: Bewusst eng und bewusst konservativ. Ein Refactor-Vorschlag, der das
#: Verhalten ändert, ist kein Refactor mehr, sondern eine Änderung — und eine
#: Änderung gehört in den Editor mit Auswirkungsdialog, nicht hierher.
SYSTEM_PROMPT = """Du schlägst ein Refactoring für ein Symbol aus einer Codebasis vor.

Was du darfst: Code verschieben, umbenennen, in kleinere Funktionen zerlegen,
Duplikate zusammenführen. Was du nicht darfst: das Verhalten ändern, neue
Abhängigkeiten einführen, öffentliche Signaturen brechen.

Regeln:
1. Antworte auf Deutsch. Beginne mit einer kurzen Begründung (maximal drei Sätze),
   was du änderst und warum das Verhalten gleich bleibt.
2. Nenne dann die Dateien als JSON-Objekt der Form
   {"begruendung": "...", "dateien": [{"pfad": "rel/pfad.py", "inhalt": "..."}], "geloescht": ["rel/pfad.py"]}.
3. Jeder Pfad ist relativ zum Projektroot. Schreibe nur Pfade innerhalb des Projekts.
4. Gib für jede zu ändernde Datei den *vollständigen* neuen Inhalt, nicht Zeilenoperationen.
5. Gib nur das JSON, keinen Text davor oder danach, keine Markdown-Umrandung.
6. Wenn du die Datei nicht kennst, erfinde nichts — nenne sie nicht.

Belegregel wie überall: eine konkrete Aussage über den Code trägt `pfad:zeile`.
"""


def _rpc_factory(project: dict[str, Any], session: str, config_path: str):
    def rpc(method: str, params: dict[str, Any] | None = None) -> Any:
        return service.query(
            project,
            method,
            {**(params or {}), "session": session},
            config_path=config_path,
        )

    return rpc


def _extract_json(text: str) -> dict[str, Any] | None:
    """Das Modell soll nur JSON liefern; falls es doch umrandet, schneide es frei."""
    s = text.strip()
    if s.startswith("```"):
        # ```json ... ``` oder ``` ... ```
        s = s.split("```", 2)
        if len(s) >= 2:
            inner = s[1]
            # führendes "json" abschneiden
            if inner.lstrip().lower().startswith("json"):
                inner = inner.lstrip()[4:]
            s = inner
        else:
            s = text
    # Erste { bis letzte } — hält auch, wenn Prosa davor/danach steht.
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None


def validate_proposal(
    root: Path, proposal: dict[str, Any]
) -> tuple[bool, list[str], dict[str, Any]]:
    """Vorschlag prüfen, ohne ihn zu schreiben.

    Vier Prüfungen, bevor irgendetwas geschrieben wird:

    1. Jeder Pfad durch :func:`resolve_within` — ein Vorschlag außerhalb des
       Projekts wird verworfen, nicht korrigiert. ``../../etc/passwd`` fällt hier.
    2. JSON-Form: ``dateien`` (Liste aus ``pfad``/``inhalt``), ``geloescht`` (Liste).
    3. Syntaxprüfung für Python-Dateien über ``compile()``; andere Sprachen
       werden im Sandbox-Reindex geprüft (Teil D), nicht hier.
    4. Ein leerer Vorschlag (keine Dateien, nichts gelöscht) ist kein Vorschlag.

    Gibt ``(ok, fehler, bereinigt)`` zurück. ``bereinigt`` enthält nur Pfade,
    die die erste Prüfung bestanden haben — ein Pfad, der das Projekt verlässt,
    wird nicht einmal in die Syntaxprüfung geschickt.
    """
    errors: list[str] = []
    files_in: list[dict[str, str]] = []
    if not isinstance(proposal, dict):
        return False, ["Vorschlag ist kein JSON-Objekt"], {}
    dateien = proposal.get("dateien") or []
    geloescht = proposal.get("geloescht") or []
    if not isinstance(dateien, list) or not isinstance(geloescht, list):
        return False, ["`dateien`/`geloescht` fehlen oder sind keine Listen"], {}
    if not dateien and not geloescht:
        return False, ["Vorschlag enthält keine Änderungen"], {}

    for entry in dateien:
        if not isinstance(entry, dict):
            errors.append("Datei-Eintrag ist kein Objekt")
            continue
        pfad = entry.get("pfad")
        inhalt = entry.get("inhalt")
        if not isinstance(pfad, str) or not pfad:
            errors.append("Datei-Eintrag ohne Pfad")
            continue
        if not isinstance(inhalt, str):
            errors.append(f"{pfad}: kein Dateiinhalt")
            continue
        try:
            resolved = workspace_manager.resolve_within(root, pfad)
        except WorkspaceError as exc:
            errors.append(f"{pfad}: {exc}")
            continue
        # Prüfung 3: Python-Syntax. Andere Sprachen werden beim Reindex der
        # Sandbox geprüft (ein Parse-Fehler taucht dort als `skipped` auf).
        if resolved.suffix == ".py":
            try:
                compile(inhalt, str(resolved), "exec")
            except SyntaxError as exc:
                errors.append(
                    f"{pfad}: Syntaxfehler Zeile {exc.lineno or '?'}: {exc.msg}"
                )
                continue
        files_in.append({"pfad": pfad, "inhalt": inhalt, "resolved": str(resolved)})

    geloescht_clean: list[str] = []
    for pfad in geloescht:
        if not isinstance(pfad, str) or not pfad:
            continue
        try:
            workspace_manager.resolve_within(root, pfad)
        except WorkspaceError as exc:
            errors.append(f"löschen {pfad}: {exc}")
            continue
        geloescht_clean.append(pfad)

    cleaned = {
        "begruendung": proposal.get("begruendung") or "",
        "dateien": [{"pfad": f["pfad"], "inhalt": f["inhalt"]} for f in files_in],
        "geloescht": geloescht_clean,
    }
    return (len(errors) == 0), errors, cleaned


def apply_to_worktree(worktree: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    """Vorschlag in den Sandbox-Worktree schreiben (geprüfter Vorschlag).

    Nur in den Worktree — niemals in den Hauptbaum. Die Übernahme ist ein
    eigener, bestätigter Schritt (``apply_sandbox``). Gibt je Pfad an, ob
    geschrieben/gelöscht/übersprungen wurde.
    """
    written: list[dict[str, str]] = []
    for entry in proposal.get("dateien") or []:
        pfad = entry.get("pfad")
        inhalt = entry.get("inhalt")
        if not isinstance(pfad, str) or not isinstance(inhalt, str):
            continue
        try:
            workspace_manager.write_file(worktree, pfad, inhalt)
            written.append({"path": pfad, "action": "written"})
        except (WorkspaceError, OSError) as exc:  # noqa: BLE001 — ein Pfad fällt raus
            written.append({"path": pfad, "action": "skipped", "reason": str(exc)})
    deleted: list[dict[str, str]] = []
    for pfad in proposal.get("geloescht") or []:
        if not isinstance(pfad, str):
            continue
        try:
            target = workspace_manager.resolve_within(worktree, pfad)
            if target.is_file():
                target.unlink()
                deleted.append({"path": pfad, "action": "deleted"})
            else:
                deleted.append({"path": pfad, "action": "absent"})
        except (WorkspaceError, OSError) as exc:  # noqa: BLE001
            deleted.append({"path": pfad, "action": "skipped", "reason": str(exc)})
    return {"written": written, "deleted": deleted}


def propose_stream(
    project: dict[str, Any],
    node_id: str,
    *,
    session: str = "refactor",
    provider: str | None = None,
    model: str | None = None,
    config_path: str = "config.yaml",
    router: LLMRouter | None = None,
) -> Iterator[dict[str, Any]]:
    """Vorschlag als Ereignisstrom — dieselbe Form wie ``explain_stream``."""
    rpc = _rpc_factory(project, session, config_path)
    root = Path(project.get("path") or "")

    try:
        router = router or LLMRouter.from_config_file(config_path)
    except Exception as error:  # noqa: BLE001 — muss als Ereignis ankommen
        yield {"event": "failed", "error": f"LLM nicht konfiguriert: {error}"}
        return

    provider_name = provider or router.default_provider
    model_name = model or router.provider_default_model(provider_name)

    try:
        rpc("session_reset")
        yield {"event": "activity", "text": "lese das Symbol"}
        node = rpc("tool_call", {"name": "get_node", "arguments": {"id": node_id}})
    except CodeSearchMissingError as error:
        yield {"event": "failed", "error": str(error), "kind": "binary_missing"}
        return
    except CodeGraphError as error:
        yield {"event": "failed", "error": f"Code-Graph: {error}"}
        return

    payload = node if isinstance(node, str) else json.dumps(node, ensure_ascii=False)
    if '"fehler"' in payload:
        yield {"event": "failed", "error": "Symbol nicht im Index"}
        return

    # Den Quelltext dazuholen — das Modell soll die ganze Datei sehen, nicht
    # nur den Steckbrief, sonst zerlegt es eine Funktion, ohne die Aufrufer
    # im selben Modul zu kennen.
    try:
        source = rpc("tool_call", {"name": "get_source", "arguments": {"id": node_id}})
    except CodeGraphError:
        source = ""
    src_text = (
        source if isinstance(source, str) else json.dumps(source, ensure_ascii=False)
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Symbol:\n{payload}\n\n"
                f"Quelltext (Auszug):\n{src_text}\n\n"
                "Schlage ein Refactoring vor. Gib nur das JSON."
            ),
        },
    ]

    yield {"event": "activity", "text": "formuliere den Vorschlag"}
    try:
        text, _calls = router.chat_with_tools(
            messages,
            None,
            provider=provider_name,
            overrides=model_overrides(router, provider_name, model_name),
        )
    except Exception as error:  # noqa: BLE001
        yield {"event": "failed", "error": str(error)}
        return

    text = (text or "").strip()
    if not text:
        yield {"event": "failed", "error": "Das Modell hat keinen Vorschlag geliefert"}
        return

    proposal = _extract_json(text)
    if proposal is None:
        yield {
            "event": "failed",
            "error": "Vorschlag ist kein gültiges JSON",
            "raw": text[:2000],
        }
        return

    ok, errors, cleaned = validate_proposal(root, proposal)
    yield {
        "event": "done",
        "proposal": {
            "node_id": node_id,
            "begruendung": cleaned.get("begruendung")
            or proposal.get("begruendung")
            or "",
            "dateien": cleaned["dateien"],
            "geloescht": cleaned["geloescht"],
            "valid": ok,
            "errors": errors,
            "provider": provider_name,
            "model": model_name,
        },
    }


def propose(project: dict[str, Any], node_id: str, **kwargs: Any) -> dict[str, Any]:
    """Nicht-strömende Fassung für Tests."""
    result: dict[str, Any] = {}
    for event in propose_stream(project, node_id, **kwargs):
        if event.get("event") == "done":
            result = event["proposal"]
        elif event.get("event") == "failed":
            raise RuntimeError(event.get("error") or "Vorschlag fehlgeschlagen")
    return result
