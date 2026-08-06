"""Vergleichsmessung fuer Code-Graph-Prompts gegen ein echtes Modell.

Der Begleiter wurde laut Projektgedächtnis nie gegen ein echtes Modell gefahren.
„Besser" ist ohne Messung eine Behauptung — dieser Skript legt das PaperKG-Repo
selbst als Werkstatt-Projekt an, indiziert es und stellt feste Fragen, deren
Antwort sich aus dem Code nachweisen lässt. Je Antwort protokolliert es:

  * ``verdict`` und ``is_clean`` aus der Rust-Belegprüfung (``verify_citations``)
  * Anzahl Belege, Anzahl nackter ``[1]``-Verweise, Werkzeugrunden
  * ``reasoning_truncated`` (ein Reasoning-Modell, das sein Budget im Denken
    verbraucht, ist der gemessene Grund fuer „keine Antwort")
  * Dauer

Zwei Läufe (alter/neuer Prompt, oder zwei Modelle) sind vergleichbar, weil die
Prüfung in Rust sitzt und nicht vom Modell abhängt.

Benutzung::

    python scripts/codegraph_prompt_check.py --provider ollama --model deepseek-v4-flash:cloud
    python scripts/codegraph_prompt_check.py --label neu

Indiziert ist ein Cache unter ``data/codegraph/...``; ein zweiter Lauf nimmt
den vorhandenen Index. Ohne ``cs``-Binary bricht das Skript mit dem Bauhinweis
ab — es ist kein Defekt, sondern fehlende Voraussetzung.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from codegraph import binary, service  # noqa: E402
from codegraph.companion import ask  # noqa: E402
from query.llm_router import LLMRouter  # noqa: E402
from storage.metadata_db import MetadataDB  # noqa: E402

#: Feste Fragen ueber den PaperKG-Code selbst. Jede laesst sich aus dem Index
#: belegen — eine Antwort ohne ``datei:zeile``-Beleg ist also ein Qualitaets-
#: fehler, kein schwerer Fall.
QUESTIONS = [
    "Wo werden Zitate in Antworten auf Code verifiziert?",
    "Wo wird der alternative git-Index fuer Checkpoints angelegt?",
    "Wo wird ein PDF im Kindprozess geparst?",
    "Wo wird die Projekt-Sperrung (InstanceLock) umgesetzt?",
    "Wo werden extrahierte Entitaeten in DuckDB gespeichert?",
    "Wo wird der Systemprompt des Code-Begleiters definiert?",
    "Wo laeuft die Werkzeugschleife des Begleiters?",
    "Wo wird ein Dateiinhalt per sha256 gehasht (content_hash)?",
    "Wo werden arXiv-Papiere geholt?",
    "Wo wird das Tauri-Fenster fuer den Overlay geoeffnet?",
]


def _register_project(db: MetadataDB, path: Path) -> dict[str, Any]:
    """PaperKG selbst als externes Werkstatt-Projekt registrieren (idempotent)."""
    for project in db.list_code_projects():
        if Path(project["path"]).resolve() == path.resolve():
            return project
    return db.add_code_project(
        name="PaperKG-Prompt-Check", path=str(path), kind="external"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", default=None, help="LLM-Provider (sonst config-Default)"
    )
    parser.add_argument(
        "--model", default=None, help="Modellname (sonst Provider-Default)"
    )
    parser.add_argument("--label", default="run", help="Label fuer die Ausgabedatei")
    parser.add_argument("--metadata-db", default="data/metadata.duckdb")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--limit", type=int, default=None, help="Nur die ersten N Fragen"
    )
    args = parser.parse_args()

    if not binary.binary_available():
        print(binary.BUILD_HINT, file=sys.stderr)
        return 2

    router = LLMRouter.from_config_file(args.config)
    db = MetadataDB(args.metadata_db)
    project = _register_project(db, REPO_ROOT)

    # Index vorhanden? Sonst einmal bauen (kann dauern).
    status = service.status(db, project, config_path=args.config)
    if not status.get("index_exists") or status.get("status") != "ready":
        print(f"Indiziere {REPO_ROOT.name} …", file=sys.stderr)
        for event in service.index(db, project, config_path=args.config):
            if event.get("event") == "progress":
                print(f"  {event.get('text', '')}", file=sys.stderr)
            elif event.get("event") == "failed":
                print(
                    f"Indizierung fehlgeschlagen: {event.get('error')}", file=sys.stderr
                )
                return 3

    questions = QUESTIONS if args.limit is None else QUESTIONS[: args.limit]
    results: list[dict[str, Any]] = []
    for question in questions:
        start = time.monotonic()
        try:
            answer = ask(
                project,
                question,
                provider=args.provider,
                model=args.model,
                config_path=args.config,
                metadata_db_path=args.metadata_db,
                router=router,
            )
            record = {
                "question": question,
                "verdict": answer.get("verdict"),
                "verdict_label": answer.get("verdict_label"),
                "is_clean": answer.get("is_clean"),
                "citations": len(answer.get("citations") or []),
                "bare_citations": answer.get("bare_citations"),
                "tool_calls": answer.get("tool_calls"),
                "truncated": answer.get("truncated"),
                "reasoning_truncated": bool(
                    router.last_response_metadata.get("reasoning_truncated")
                ),
                "uncited_sentences": answer.get("uncited_sentences"),
                "seconds": round(time.monotonic() - start, 1),
                "answer": (answer.get("answer") or "")[:400],
            }
        except Exception as error:  # noqa: BLE001 — ein Durchfall, nicht Abbruch
            record = {
                "question": question,
                "failed": str(error),
                "reasoning_truncated": bool(
                    router.last_response_metadata.get("reasoning_truncated")
                ),
                "seconds": round(time.monotonic() - start, 1),
            }
        results.append(record)
        summary = record.get("verdict_label") or record.get("failed") or "?"
        print(f"[{record['seconds']}s] {summary}: {question}", file=sys.stderr)

    out = Path("data/eval") / f"codegraph_prompt_{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "provider": args.provider,
                "model": args.model,
                "questions": len(results),
                "clean": sum(1 for r in results if r.get("is_clean")),
                "reasoning_truncated": sum(
                    1 for r in results if r.get("reasoning_truncated")
                ),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nGeschrieben: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
