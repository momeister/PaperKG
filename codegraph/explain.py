"""Ein einzelnes Symbol erklären lassen — mit denselben Belegregeln wie sonst.

Warum das nicht einfach eine Frage an den Begleiter ist: der baut seinen Kontext
aus einer *Suche* (``context_build``), und „erkläre mir dieses Symbol" ist keine
Suche — das Symbol ist schon bekannt. Hier wird stattdessen gezielt
nachgeschlagen (``get_node`` liefert Fakten *und* Quelltext), und genau dieses
Nachschlagen ist zugleich die Lizenz zum Zitieren: was das Modell hier bekommen
hat, darf es belegen, sonst nichts. Die Prüfung läuft anschliessend über
dieselbe Rust-Seite wie bei jeder anderen Antwort.

Ohne LLM ist trotzdem nichts kaputt: die Oberfläche zeigt weiterhin ihren
Faktensteckbrief; diese Route meldet dann einen Fehler und sonst nichts.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from codegraph import service
from codegraph.binary import CodeSearchMissingError
from codegraph.companion import _utf16_offsets
from codegraph.llm_prefs import model_overrides
from codegraph.rpc import CodeGraphError
from query.llm_router import LLMRouter

#: Bewusst eng. Eine Erklärung, die ausschweift, ist an dieser Stelle nicht
#: hilfreicher, sondern nur länger — sie steht neben dem Code, nicht statt seiner.
SYSTEM_PROMPT = """Du erklärst ein einzelnes Symbol aus einer Codebasis.

Regeln:
1. Antworte auf Deutsch, in höchstens sechs Sätzen, ohne Aufzählung.
2. Sage zuerst in einem Satz, wofür das Symbol da ist — nicht, was Zeile für Zeile passiert.
3. Sage dann, was hereinkommt und was herausgeht: Parameter, Rückgabe, Nebenwirkungen.
4. Nenne genau dann eine Besonderheit, wenn es eine gibt (Sonderfälle, Fehlerbehandlung, Annahmen).
5. Belege jede konkrete Aussage über den Code mit `pfad:zeile` oder `pfad:von-bis`.
   Zitiere nur Zeilen, die dir in diesem Gespräch gezeigt wurden.
6. Was du nicht weisst, sagst du nicht. Kein „vermutlich", kein Ausschmücken.
"""


def explain_stream(
    project: dict[str, Any],
    node_id: str,
    *,
    session: str = "explain",
    provider: str | None = None,
    model: str | None = None,
    config_path: str = "config.yaml",
    router: LLMRouter | None = None,
) -> Iterator[dict[str, Any]]:
    """Erklärung als Ereignisstrom — dieselbe Form wie ``ask_stream``."""

    def rpc(method: str, params: dict[str, Any] | None = None) -> Any:
        return service.query(
            project, method, {**(params or {}), "session": session}, config_path=config_path
        )

    try:
        router = router or LLMRouter.from_config_file(config_path)
    except Exception as error:  # noqa: BLE001 — muss als Ereignis ankommen
        yield {"event": "failed", "error": f"LLM nicht konfiguriert: {error}"}
        return

    provider_name = provider or router.default_provider
    model_name = model or router.provider_default_model(provider_name)

    try:
        # Neue Erklärung, neue Sitzung: eine alte Lizenz zum Zitieren darf nicht
        # auf diese Antwort durchschlagen.
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

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{payload}\n\nErkläre dieses Symbol."},
    ]

    yield {"event": "activity", "text": "formuliere die Erklärung"}
    try:
        # Ohne Werkzeuge: alles, was zählt, steht schon im Kontext, und ein
        # kleines lokales Modell fängt sonst an, im Kreis nachzuschlagen.
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
        yield {"event": "failed", "error": "Das Modell hat keine Erklärung geliefert"}
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
            "text": checked,
            "citations": citations,
            "verdict": verified.get("verdict"),
            "verdict_label": verified.get("verdict_label"),
            "is_clean": bool(verified.get("is_clean")),
            "quote_mismatches": verified.get("quote_mismatches") or [],
            "provider": provider_name,
            "model": model_name,
        },
    }


def explain(project: dict[str, Any], node_id: str, **kwargs: Any) -> dict[str, Any]:
    """Nicht-strömende Fassung für Tests und Aufrufer ohne SSE."""
    result: dict[str, Any] = {}
    for event in explain_stream(project, node_id, **kwargs):
        if event.get("event") == "done":
            result = event["answer"]
        elif event.get("event") == "failed":
            raise RuntimeError(event.get("error") or "Erklärung fehlgeschlagen")
    return result
