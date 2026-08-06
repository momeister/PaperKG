"""Die Bereiche eines Projekts: Struktur aus dem Index, Namen vom Modell.

Der Einstieg in eine fremde Codebasis ist nicht „was macht diese Funktion",
sondern „woraus besteht das hier". Genau das beantwortet die Cluster-Landkarte:
oberste Ebene sind die Ordner, dazwischen stehen die aufsummierten Beziehungen,
ein Klick geht eine Ebene tiefer.

**Die Arbeitsteilung ist der Punkt.** Die Struktur — welche Bereiche es gibt,
wie viele Symbole darin liegen, wie oft sie einander aufrufen, wie sicher das
ist — kommt aus SQL über den Index (``Graph::clusters`` in Rust) und ist damit
prüfbar und vom Modell nicht beeinflussbar. Das Modell vergibt ausschliesslich
**Namen und Zweck-Satz**, und es sieht dafür nur Kennzahlen, keinen Quelltext.
Fällt es aus, fehlt der Name und sonst nichts: die Karte zeigt Ordnernamen und
funktioniert vollständig.

Deshalb gibt es hier auch keine Zitatprüfung. Es wird nichts belegt — es wird
etwas beschriftet.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

from codegraph import service
from codegraph.binary import CodeSearchMissingError
from codegraph.llm_prefs import model_overrides
from codegraph.rpc import CodeGraphError
from query.llm_router import LLMRouter

#: Mehr Bereiche als das auf einmal zu benennen sprengt den Prompt und bringt
#: nichts: eine Ebene mit vierzig Ordnern ist ohnehin keine Übersicht mehr.
MAX_LABELLED = 40

SYSTEM_PROMPT = """Du benennst die Bereiche eines Softwareprojekts.

Du bekommst je Bereich den Ordnerpfad, die Zahl der Symbole, ein Histogramm der
Symbolarten und die wichtigsten Symbolnamen. Mehr nicht — insbesondere keinen
Quelltext.

Regeln:
1. Antworte ausschliesslich mit JSON: {"bereiche": [{"pfad": "...", "name": "...", "zweck": "..."}]}
2. `name` ist deutsch, zwei bis drei Wörter, benennt die Aufgabe des Bereichs
   (etwa "Datenbeschaffung", "Antwortgabe mit Belegen"), nicht seinen Ordnernamen.
3. `zweck` ist genau ein deutscher Satz: wofür dieser Bereich im Ganzen da ist.
4. Gib zu jedem übergebenen Pfad genau einen Eintrag zurück, mit dem Pfad wörtlich.
5. Wenn die Angaben für einen Bereich nichts hergeben, nimm den Ordnernamen als
   `name` und schreibe als `zweck` "Lässt sich aus dem Index nicht ableiten."
   Rate nicht.
"""


def fingerprint(cluster: dict[str, Any]) -> str:
    """Die *Gestalt* eines Bereichs als kurzer Hash.

    Bewusst nicht der Dateiinhalt: sonst verlöre ein Bereich seinen Namen,
    sobald irgendwo eine Zeile geändert wird. Was den Namen entwertet, ist eine
    veränderte Gestalt — andere Symbolzahl, andere Artenverteilung, andere
    Hauptsymbole.
    """
    parts = [
        str(cluster.get("path") or ""),
        str(cluster.get("symbols") or 0),
        str(cluster.get("files") or 0),
        ",".join(f"{kind}:{count}" for kind, count in (cluster.get("kinds") or [])),
        ",".join(
            str(symbol.get("qualified") or "")
            for symbol in (cluster.get("top_symbols") or [])
        ),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def level(
    project: dict[str, Any],
    *,
    prefix: str = "",
    edge_kinds: list[str] | None = None,
    config_path: str = "config.yaml",
) -> dict[str, Any]:
    """Eine Ebene der Landkarte, roh aus dem Index."""
    params: dict[str, Any] = {"prefix": prefix}
    if edge_kinds:
        params["edge_kinds"] = edge_kinds
    return service.query(project, "clusters", params, config_path=config_path) or {}


def apply_labels(
    payload: dict[str, Any], stored: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Hinterlegte Namen an die Bereiche hängen — und veraltete kennzeichnen.

    Ein Name, dessen Fingerabdruck nicht mehr passt, wird **nicht** stillschweigend
    weiterverwendet und auch nicht weggeworfen: er bleibt sichtbar, aber als
    ``label_stale`` markiert. Wegwerfen hiesse, dass die Karte nach jedem
    Reindizieren nackt dasteht; stillschweigend behalten hiesse, eine Behauptung
    über einen Bereich zu machen, den es so nicht mehr gibt.
    """
    for cluster in payload.get("nodes") or []:
        record = stored.get(str(cluster.get("path") or ""))
        current = fingerprint(cluster)
        cluster["fingerprint"] = current
        if not record:
            cluster["label_source"] = "directory"
            cluster["purpose"] = None
            cluster["label_stale"] = False
            continue
        cluster["label"] = record.get("label") or cluster.get("label")
        cluster["purpose"] = record.get("purpose")
        cluster["label_source"] = record.get("source") or "llm"
        cluster["label_stale"] = str(record.get("fingerprint") or "") != current
    return payload


def _prompt_payload(clusters: list[dict[str, Any]]) -> str:
    compact = [
        {
            "pfad": cluster.get("path"),
            "ordner": cluster.get("label"),
            "symbole": cluster.get("symbols"),
            "dateien": cluster.get("files"),
            "arten": {kind: count for kind, count in (cluster.get("kinds") or [])},
            "wichtigste": [
                symbol.get("name") for symbol in (cluster.get("top_symbols") or [])
            ],
        }
        for cluster in clusters
    ]
    return json.dumps({"bereiche": compact}, ensure_ascii=False, indent=1)


def name_level_stream(
    project: dict[str, Any],
    *,
    prefix: str = "",
    code_project_id: str,
    db: Any,
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
    config_path: str = "config.yaml",
    router: LLMRouter | None = None,
) -> Iterator[dict[str, Any]]:
    """Eine ganze Ebene auf einmal benennen lassen.

    Ein Aufruf für die Ebene, nicht einer je Bereich: die Bereiche eines
    Projekts erklären sich gegenseitig — wer weiss, dass daneben „Beschaffung"
    steht, nennt den Nachbarn nicht auch so.
    """
    try:
        payload = level(project, prefix=prefix, config_path=config_path)
    except CodeSearchMissingError as error:
        yield {"event": "failed", "error": str(error), "kind": "binary_missing"}
        return
    except CodeGraphError as error:
        yield {"event": "failed", "error": f"Code-Graph: {error}"}
        return

    clusters = list(payload.get("nodes") or [])
    if not clusters:
        yield {
            "event": "done",
            "labels": [],
            "skipped": "keine Bereiche auf dieser Ebene",
        }
        return

    stored = db.get_cluster_labels(code_project_id)
    todo = [
        cluster
        for cluster in clusters[:MAX_LABELLED]
        if force
        or str(stored.get(str(cluster.get("path")), {}).get("fingerprint") or "")
        != fingerprint(cluster)
    ]
    if not todo:
        yield {"event": "done", "labels": [], "skipped": "alle Namen sind aktuell"}
        return

    try:
        router = router or LLMRouter.from_config_file(config_path)
    except Exception as error:  # noqa: BLE001 — muss als Ereignis ankommen
        yield {"event": "failed", "error": f"LLM nicht konfiguriert: {error}"}
        return

    provider_name = provider or router.default_provider
    model_name = model or router.provider_default_model(provider_name)

    yield {"event": "activity", "text": f"benenne {len(todo)} Bereiche"}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _prompt_payload(todo)},
    ]
    try:
        answer = router.chat_json(
            messages,
            provider=provider_name,
            overrides=model_overrides(router, provider_name, model_name),
        )
    except Exception as error:  # noqa: BLE001 — als Ereignis, nicht als 500
        yield {"event": "failed", "error": str(error)}
        return

    # Das Modell darf keine Bereiche erfinden und keine umbenennen, die es nicht
    # bekommen hat: nur Pfade aus `todo` werden übernommen.
    wanted = {str(cluster.get("path")): cluster for cluster in todo}
    written: list[dict[str, Any]] = []
    for item in (answer or {}).get("bereiche") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("pfad") or "")
        cluster = wanted.get(path)
        if cluster is None:
            continue
        label = str(item.get("name") or "").strip()
        if not label:
            continue
        record = db.upsert_cluster_label(
            code_project_id,
            path,
            fingerprint=fingerprint(cluster),
            label=label[:120],
            purpose=(str(item.get("zweck") or "").strip() or None),
            source="llm",
            provider=provider_name,
            model=model_name,
        )
        if record:
            written.append(record)

    if not written:
        yield {
            "event": "failed",
            "error": "Das Modell hat keine verwertbaren Namen geliefert",
        }
        return

    yield {
        "event": "done",
        "labels": written,
        "provider": provider_name,
        "model": model_name,
    }
