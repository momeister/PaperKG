"""„Warum wurde das so gebaut?" — getrennt nach *aufgezeichnet* und *hergeleitet*.

**Der Ehrlichkeitsvorbehalt steht am Anfang, weil er die Bauform bestimmt:** der
Prompt einer erzeugenden KI ist in diesem Repository nirgends aufgezeichnet. Ein
Werkzeug, das so täte, als könne es ihn rekonstruieren, wäre genau die Art
unbelegter Behauptung, gegen die der ganze Rest gebaut ist — und es wäre die
gefährlichste davon, weil eine erfundene Absicht sich nicht wie ein erfundenes
`datei:zeile` nachprüfen lässt.

Die Antwort hat deshalb zwei Hälften, die nie vermischt werden:

* **Aufgezeichnet** (:func:`recorded`) — ``git log -L`` über genau diese Zeilen
  und die selbst hinterlegten Begründungen aus ``code_rationale``. Beides ist
  Beleg, beides braucht kein Modell, beides ist auch ohne Netz da.
* **Hergeleitet** (:func:`why_stream`) — zwei Sätze eines Modells, aus Code,
  Doku, Tests und Aufrufern. Als Herleitung gekennzeichnet, mit derselben
  Belegprüfung wie jede andere Antwort, und **nicht gespeichert**: eine
  Herleitung veraltet mit der nächsten Änderung, genau wie ``explain``.

Tests werden über die **Symbolart** der Aufrufer gefunden (``NodeKind::Test``),
nicht über die Kantenart ``tested_by``: die ist in ``cs-core`` zwar deklariert,
wird aber nirgends erzeugt (nachgeprüft: ``grep -rn "TestedBy"`` außerhalb von
``cs-core`` ist leer). Eine Abfrage darauf sähe aus wie „keine Tests" und wäre
eine Falschaussage über den Code.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from codegraph import service
from codegraph.binary import CodeSearchMissingError
from codegraph.companion import _utf16_offsets
from codegraph.llm_prefs import model_overrides
from codegraph.rpc import CodeGraphError
from query.llm_router import LLMRouter
from workspace import manager as workspace_manager

#: Wie viele Commits über den Zeilen einer Funktion noch etwas erzählen. Darüber
#: hinaus liest niemand mehr, und der Prompt wird nur teurer.
MAX_COMMITS = 12

#: Aufrufer im Prompt. Die Frage ist „wofür ist das da", nicht „wer benutzt es" —
#: dafür reichen die ersten paar, und Tests werden ohnehin gesondert genannt.
MAX_CALLERS = 12

SYSTEM_PROMPT = """Du beantwortest zu einer Stelle im Code die Frage: warum wurde das so gebaut?

Regeln:
1. Antworte auf Deutsch in **genau zwei Sätzen**. Keine Aufzählung, keine Überschrift.
2. Satz eins: wofür diese Stelle da ist. Satz zwei: warum sie so aussieht, wie sie aussieht
   (Sonderfall, Einschränkung, Aufrufer, Test, Commit-Betreff).
3. Stütze dich **nur** auf das, was dir gezeigt wurde: Quelltext, Doku, Aufrufer, Tests,
   Commit-Betreffe. Behaupte keine Absicht, die daraus nicht folgt.
4. Rate nicht, wer es geschrieben hat oder was jemand gedacht hat. Es gibt keine Aufzeichnung
   davon, und eine erfundene Absicht ist schlimmer als keine.
5. Folgt aus dem Vorliegenden nichts, ist die richtige Antwort genau dieser eine Satz:
   "Aus Code, Tests und Historie lässt sich kein Grund herleiten."
6. Belege konkrete Aussagen über den Code mit `pfad:zeile`. Zitiere nur Zeilen, die dir in
   diesem Gespräch gezeigt wurden.
"""


def recorded(
    project: dict[str, Any],
    *,
    rel_path: str,
    start_line: int,
    end_line: int,
    code_project_id: str,
    db: Any = None,
    symbol_id: str | None = None,
) -> dict[str, Any]:
    """Die belegte Hälfte: git über diese Zeilen und was jemand hinterlegt hat.

    Braucht kein Modell und kein Netz. Fehlt git oder ist die Datei nie
    committet worden, steht das als Grund da — statt als leere Liste, die wie
    „es gab keine Änderungen" aussähe.
    """
    root = Path(str(project.get("path") or ""))
    history = workspace_manager.git_log_for_lines(
        root, rel_path, start_line, end_line, limit=MAX_COMMITS
    )
    notes: list[dict[str, Any]] = []
    if db is not None:
        notes = db.list_code_rationale(code_project_id, rel_path=rel_path)
        if symbol_id:
            # Begründungen zum Symbol zählen auch dann, wenn die Datei inzwischen
            # anders heisst — und umgekehrt.
            known = {note["id"] for note in notes}
            notes += [
                note
                for note in db.list_code_rationale(code_project_id, symbol_id=symbol_id)
                if note["id"] not in known
            ]
    return {"history": history, "notes": notes}


def _caller_summary(
    rpc: Any, node_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aufrufer, getrennt in Tests und alles andere.

    Über ``neighbours`` statt über das Werkzeug ``callers_of``: nur diese Antwort
    trägt die **Symbolart** mit, und ohne die liesse sich ein Test nicht von
    einem gewöhnlichen Aufrufer unterscheiden.
    """
    try:
        neighbours = (
            rpc(
                "neighbours",
                {"id": node_id, "direction": "in", "edge_kinds": ["calls"]},
            )
            or []
        )
    except CodeGraphError:
        return [], []

    tests: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for neighbour in neighbours:
        node = neighbour.get("node") or {}
        entry = {
            "qualified": node.get("qualified"),
            "path": node.get("path"),
            "line": node.get("line"),
            "confidence": neighbour.get("confidence"),
            "evidence": f"{neighbour.get('evidence_path')}:{neighbour.get('evidence_line')}",
        }
        (tests if node.get("kind") == "test" else others).append(entry)
    return tests[:MAX_CALLERS], others[:MAX_CALLERS]


def why_stream(
    project: dict[str, Any],
    node_id: str,
    *,
    code_project_id: str,
    db: Any = None,
    session: str = "why",
    provider: str | None = None,
    model: str | None = None,
    config_path: str = "config.yaml",
    router: LLMRouter | None = None,
) -> Iterator[dict[str, Any]]:
    """Die hergeleitete Hälfte als Ereignisstrom. Gebaut wie ``explain_stream``."""

    def rpc(method: str, params: dict[str, Any] | None = None) -> Any:
        return service.query(
            project,
            method,
            {**(params or {}), "session": session},
            config_path=config_path,
        )

    try:
        router = router or LLMRouter.from_config_file(config_path)
    except Exception as error:  # noqa: BLE001 — muss als Ereignis ankommen
        yield {"event": "failed", "error": f"LLM nicht konfiguriert: {error}"}
        return

    provider_name = provider or router.default_provider
    model_name = model or router.provider_default_model(provider_name)

    try:
        # Neue Herleitung, neue Sitzung: eine alte Lizenz zum Zitieren darf nicht
        # auf diese Antwort durchschlagen.
        rpc("session_reset")
        yield {"event": "activity", "text": "lese die Stelle"}
        node = rpc("tool_call", {"name": "get_node", "arguments": {"id": node_id}})
        detail = rpc("node", {"id": node_id})
    except CodeSearchMissingError as error:
        yield {"event": "failed", "error": str(error), "kind": "binary_missing"}
        return
    except CodeGraphError as error:
        yield {"event": "failed", "error": f"Code-Graph: {error}"}
        return

    payload = node if isinstance(node, str) else json.dumps(node, ensure_ascii=False)
    if not detail or '"fehler"' in payload:
        yield {"event": "failed", "error": "Symbol nicht im Index"}
        return

    span = detail.get("span") or {}
    rel_path = str(detail.get("path") or "")
    start_line = int(span.get("start_line") or 1)
    end_line = int(span.get("end_line") or start_line)

    yield {"event": "activity", "text": "sehe nach, wer das benutzt"}
    tests, callers = _caller_summary(rpc, node_id)

    yield {"event": "activity", "text": "lese die Historie dieser Zeilen"}
    facts = recorded(
        project,
        rel_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        code_project_id=code_project_id,
        db=db,
        symbol_id=node_id,
    )

    # Commit-Betreffe sind das ehrlichste Signal für eine Absicht, das es hier
    # gibt — geschrieben von Menschen, über genau diese Zeilen. Sie gehen als
    # *Aufzeichnung* in den Prompt, ausdrücklich benannt, damit das Modell sie
    # nicht als eigene Herleitung ausgibt.
    context = {
        "symbol": node,
        "aufrufende_tests": tests,
        "weitere_aufrufer": callers,
        "commit_betreffe": [
            f"{commit['date']} {commit['subject']}"
            for commit in facts["history"]["commits"]
        ],
        "hinterlegte_begruendungen": [note["text"] for note in facts["notes"]],
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{json.dumps(context, ensure_ascii=False)}\n\n"
                "Warum wurde diese Stelle so gebaut?"
            ),
        },
    ]

    yield {"event": "activity", "text": "leite ab"}
    try:
        # Ohne Werkzeuge: alles Nötige steht im Kontext, und ein kleines lokales
        # Modell fängt sonst an, im Kreis nachzuschlagen.
        text, _calls = router.chat_with_tools(
            messages,
            None,
            provider=provider_name,
            overrides=model_overrides(router, provider_name, model_name),
        )
    except Exception as error:  # noqa: BLE001 — als Ereignis, nicht als 500
        yield {"event": "failed", "error": str(error)}
        return

    text = (text or "").strip()
    if not text:
        yield {"event": "failed", "error": "Das Modell hat keine Herleitung geliefert"}
        return

    yield {"event": "activity", "text": "prüfe die Belege"}
    try:
        verified = rpc("verify_citations", {"text": text}) or {}
    except CodeGraphError as error:
        yield {"event": "failed", "error": f"Beleg-Prüfung fehlgeschlagen: {error}"}
        return

    checked = str(verified.get("text") or text)
    citations = []
    for citation in verified.get("citations") or []:
        start, end = _utf16_offsets(
            checked, int(citation.get("start") or 0), int(citation.get("end") or 0)
        )
        citations.append({**citation, "start": start, "end": end})

    yield {
        "event": "done",
        "answer": {
            "node_id": node_id,
            "path": rel_path,
            "start_line": start_line,
            "end_line": end_line,
            # Ausdrücklich benannt, nicht nur eine Zeichenkette: die Oberfläche
            # muss diesen Block als *Herleitung* beschriften können, und was
            # hergeleitet ist, darf nie wie eine Aufzeichnung aussehen.
            "kind": "derived",
            "text": checked,
            "citations": citations,
            "verdict": verified.get("verdict"),
            "verdict_label": verified.get("verdict_label"),
            "is_clean": bool(verified.get("is_clean")),
            "quote_mismatches": verified.get("quote_mismatches") or [],
            "based_on": {
                "tests": len(tests),
                "callers": len(callers),
                "commits": len(facts["history"]["commits"]),
                "notes": len(facts["notes"]),
            },
            "provider": provider_name,
            "model": model_name,
        },
    }


def why(project: dict[str, Any], node_id: str, **kwargs: Any) -> dict[str, Any]:
    """Nicht-strömende Fassung für Tests und Aufrufer ohne SSE."""
    result: dict[str, Any] = {}
    for event in why_stream(project, node_id, **kwargs):
        if event.get("event") == "done":
            result = event["answer"]
        elif event.get("event") == "failed":
            raise RuntimeError(event.get("error") or "Herleitung fehlgeschlagen")
    return result
