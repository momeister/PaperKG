"""Code-Graph: Symbole, Beziehungen und Belege zu einem Werkstatt-Projekt.

Die Analyse liegt in Rust (``codesearch/``) und läuft als Kindprozess; dieses
Modul ist nur die HTTP-Schicht darüber. Die tragende Regel des Werkzeugs gilt
auch hier: **keine Beziehung ohne Belegstelle und Sicherheitsstufe**. Wer eine
Kante anzeigt, muss `verifiziert`/`aufgelöst`/`vermutet` und das `datei:zeile`
daneben zeigen können — die Antworten tragen beides, und niemand darf es
unterwegs wegkürzen.

Fehlt das ``cs``-Binary, sind das hier keine 500er: Der Code-Graph ist ein
Zusatz. Der Statusaufruf sagt dann, wie man ihn baut, und alles andere im
Programm läuft weiter.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from codegraph import service
from codegraph.binary import CodeSearchMissingError
from codegraph.rpc import CodeGraphError
from storage.metadata_db import MetadataDB
from workspace import manager as workspace_manager
from workspace import checkpoints as workspace_checkpoints
from workspace import sandbox as workspace_sandbox

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

# Spiegel von ``EdgeKind``/``NodeKind`` in ``cs-core/src/lib.rs`` (Zeilen 140-236).
# Die Listen stehen hier, damit ein Tippfehler eine 400 ergibt statt eines
# Ergebnisses: ``EdgeKind::from_str`` auf der Rust-Seite verwirft unbekannte
# Namen still (``serve.rs`` ~700, ``filter_map``), und der leere Rest fällt dann
# auf einen Standardsatz zurück — ``?edges=call`` sähe aus, als hätte es
# funktioniert. Ändert sich Rust, ändert sich das hier mit; abgesichert durch
# ``test_the_python_kind_lists_match_the_binary``.
ALL_EDGE_KINDS = (
    "contains",
    "calls",
    "imports",
    "inherits",
    "implements",
    "reads",
    "writes",
    "param_type",
    "returns_type",
    "throws",
    "tested_by",
    "touches_table",
    "handles_route",
    "gated_by",
)
ALL_NODE_KINDS = (
    "file",
    "module",
    "class",
    "interface",
    "function",
    "method",
    "field",
    "global",
    "route",
    "db_table",
    "db_column",
    "test",
    "config_key",
    "external_package",
    "dynamic_gap",
)
DIRECTIONS = ("in", "out", "both")

router = APIRouter()


def _kind_list(raw: str | None, allowed: tuple[str, ...], label: str) -> list[str]:
    """Kommaliste von Arten prüfen.

    Leer heißt „keine Angabe" (die Rust-Seite setzt dann ihren eigenen Standard
    ein — welcher, steht bei der jeweiligen Route). ``all`` ist die ausdrückliche
    Vollauswahl. Alles andere muss namentlich bekannt sein.
    """
    if raw is None or not raw.strip():
        return []
    if raw.strip() == "all":
        return list(allowed)
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [part for part in wanted if part not in allowed]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte {label}: {', '.join(unknown)}. Erlaubt: {', '.join(allowed)}",
        )
    return wanted


def _direction(value: str) -> str:
    if value not in DIRECTIONS:
        raise HTTPException(
            status_code=400, detail="direction muss 'in', 'out' oder 'both' sein"
        )
    return value


class PositionsRequest(BaseModel):
    """Terminal-Ausgabe, in der Sprungmarken gesucht werden."""

    text: str = Field(default="", max_length=200_000)
    limit: int = Field(default=8, ge=1, le=40)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class AskRequest(BaseModel):
    """Eine Frage an den Code-Begleiter."""

    question: str = Field(min_length=1, max_length=4000)
    #: Ein Gespräch. Auf der Rust-Seite hängt daran die Lizenz zum Zitieren —
    #: zitiert werden darf nur, was in *dieser* Sitzung nachgeschlagen wurde.
    session: str = Field(default="default", max_length=120)
    provider: str | None = None
    model: str | None = None
    #: Forschungsprojekt, falls Papers einbezogen werden sollen.
    project_id: str | None = None
    paper_ids: list[str] | None = None
    use_papers: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ChatCreateRequest(BaseModel):
    """Ein neues Gespräch über den Code."""

    title: str | None = Field(default=None, max_length=200)
    #: Forschungsprojekt, falls Papers als zusätzliche Quelle dienen sollen.
    project_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ChatAskRequest(BaseModel):
    """Ein Zug im Gespräch."""

    question: str = Field(min_length=1, max_length=4000)
    provider: str | None = None
    model: str | None = None
    project_id: str | None = None
    paper_ids: list[str] | None = None
    use_papers: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


def _is_remote_model(model: Any) -> bool:
    """Läuft dieses Modell woanders?

    Ollamas Pro-Abo hängt ``:cloud`` an den Namen und schickt die Anfrage über
    denselben lokalen Port an fremde Server. Der Zug merkt sich das, damit
    später nachvollziehbar bleibt, welche Antwort den Rechner verlassen hat.
    Gespiegelt in ``frontend/src/components/LlmPicker.tsx::isRemoteModel``.
    """
    return isinstance(model, str) and model.endswith(":cloud")


class ClusterNameRequest(BaseModel):
    """Eine Ebene der Landkarte benennen lassen."""

    prefix: str = Field(default="", max_length=400)
    provider: str | None = None
    model: str | None = None
    #: Auch benennen, was schon einen aktuellen Namen hat.
    force: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class SymbolSourceRequest(BaseModel):
    """Der neue Text *einer Funktion* — nicht der ganzen Datei.

    Der Zeilenbereich steht bewusst **nicht** hier drin: er kommt aus dem
    Graphen. Ein Client, der eine Weile offen stand, kennt womöglich einen alten
    Bereich, und der schriebe dann an die falsche Stelle.
    """

    text: str = Field(max_length=400_000)
    #: Der Hash, den der Client beim Lesen bekommen hat — die optimistische
    #: Sperre. Passt er nicht mehr, hat inzwischen jemand anders geschrieben.
    content_hash: str = Field(min_length=8, max_length=128)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


def _splice_lines(
    original: str, start_line: int, end_line: int, replacement: str
) -> str:
    """Genau die Zeilen ``start_line..end_line`` ersetzen, den Rest byte-gleich lassen.

    Drei Dinge, die eine naive Umsetzung kaputt macht:

    * **Zeilenenden.** Monaco liefert ``\\n``; steht die Datei in CRLF, entstünde
      sonst eine Datei mit gemischten Enden, und jede Zeile darin sähe im
      git-Diff geändert aus. Das Ende des ersetzten Bereichs gibt vor, was
      geschrieben wird.
    * **Das Dateiende.** Endete die letzte ersetzte Zeile ohne Umbruch (letzte
      Zeile der Datei), darf hier keiner dazukommen.
    * **Der Rest.** Alles vor und nach dem Bereich wird unverändert
      durchgereicht, nicht neu zusammengesetzt.
    """
    lines = original.splitlines(keepends=True)
    if start_line < 1 or start_line > len(lines):
        raise HTTPException(
            status_code=409, detail="Der Zeilenbereich liegt nicht in der Datei."
        )
    end = min(max(end_line, start_line), len(lines))

    block = lines[start_line - 1 : end]
    eol = "\r\n" if any(line.endswith("\r\n") for line in block) else "\n"
    keeps_newline = block[-1].endswith(("\n", "\r"))

    body = replacement.replace("\r\n", "\n").replace("\r", "\n")
    written = [piece + eol for piece in body.split("\n")]
    if not keeps_newline:
        written[-1] = written[-1][: -len(eol)]

    return "".join(lines[: start_line - 1] + written + lines[end:])


class ExplainRequest(BaseModel):
    """Bitte um eine Erklärung zu einem Symbol."""

    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class RationaleRequest(BaseModel):
    """Eine selbst hinterlegte Begründung: „darum ist das so".

    Entweder ``symbol_id`` (dann kommt der Zeilenbereich aus dem Graphen) oder
    ``rel_path`` mit Zeilen.
    """

    text: str = Field(min_length=1, max_length=4000)
    symbol_id: str | None = None
    rel_path: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    author: str | None = Field(default=None, max_length=120)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class CiteRequest(BaseModel):
    """Eine Codestelle als Zitat an eine Notiz hängen."""

    note_id: str
    rel_path: str
    start_line: int = Field(ge=1)
    end_line: int | None = Field(default=None, ge=1)
    title: str | None = None
    reference_text: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class LinkRequest(BaseModel):
    """Verknüpfung zwischen einem Paper und Code (Projekt, Datei oder Symbol)."""

    project_id: str | None = None
    paper_id: str | None = None
    symbol_id: str | None = None
    rel_path: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    kind: str = Field(default="implements", max_length=40)
    note: str | None = Field(default=None, max_length=4000)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


def _require_code_project(db: MetadataDB, code_project_id: str) -> dict[str, Any]:
    project = db.get_code_project(code_project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Code-Projekt nicht gefunden")
    return project


def _load_project(code_project_id: str, metadata_db_path: str) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return _require_code_project(db, code_project_id)


def _sse(make_events: Callable[[], Iterator[dict[str, Any]]]) -> StreamingResponse:
    """Einen blockierenden Ereignis-Generator als SSE ausliefern.

    Der Generator läuft in einem eigenen Thread — Indizieren und die
    Werkzeugschleife blockieren beide, und der Event-Loop muss weiter Bytes
    rausschieben können, sonst kommt der Fortschritt erst mit dem Ergebnis an
    und wäre wertlos.
    """

    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def produce() -> None:
            try:
                for event in make_events():
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as error:  # noqa: BLE001 — muss als Ereignis ankommen
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"event": "failed", "error": str(error)}
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(produce))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            await task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _query(
    code_project_id: str, metadata_db_path: str, method: str, params: dict[str, Any]
) -> Any:
    """Abfrage durchreichen und Fehler der Rust-Seite auf HTTP abbilden.

    ``CodeSearchMissingError`` ist 503 und nicht 500: es ist kein Defekt, sondern
    ein nicht gebautes Zusatzteil, und der Text sagt, wie man es baut.
    """
    project = _load_project(code_project_id, metadata_db_path)
    try:
        return service.query(project, method, params)
    except CodeSearchMissingError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except CodeGraphError as error:
        raise HTTPException(status_code=502, detail=f"Code-Graph: {error}") from error


# --- Status und Index --------------------------------------------------------


@router.get("/codegraph/{code_project_id}")
def codegraph_status(
    code_project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Ist dieses Projekt indiziert, wie groß ist der Graph, wie alt ist er?

    Läuft ohne den Kindprozess — das ist der Aufruf bei jedem Projektwechsel.
    """
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, code_project_id)
        return service.status(db, project)


@router.post("/codegraph/{code_project_id}/index")
async def codegraph_index(
    code_project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> StreamingResponse:
    """Indiziert das Projekt und streamt den Fortschritt als SSE.

    Ein großes Repository braucht Sekunden bis Minuten; ohne Zwischenmeldungen
    wäre das von einem Hänger nicht zu unterscheiden. Die Phasennamen kommen
    unverändert aus der Rust-Seite.
    """

    def events() -> Iterator[dict[str, Any]]:
        with MetadataDB(metadata_db_path) as db:
            project = _require_code_project(db, code_project_id)
            yield from service.index(db, project)

    return _sse(events)


@router.delete("/codegraph/{code_project_id}/index")
def codegraph_drop_index(
    code_project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Index verwerfen. Reiner Cache — ``POST …/index`` baut ihn neu."""
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        removed = service.drop_index(db, code_project_id)
    return {"code_project_id": code_project_id, "removed": removed}


# --- Abfragen ----------------------------------------------------------------


@router.get("/codegraph/{code_project_id}/overview")
def codegraph_overview(
    code_project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Was ist das hier: Kennzahlen, wichtigste Symbole, heiße Dateien, Lücken."""
    return _query(code_project_id, metadata_db_path, "overview", {})


@router.get("/codegraph/{code_project_id}/search")
def codegraph_search(
    code_project_id: str,
    q: str,
    kind: str | None = None,
    limit: int = 40,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> list[dict[str, Any]]:
    kinds = [part.strip() for part in (kind or "").split(",") if part.strip()]
    return _query(
        code_project_id,
        metadata_db_path,
        "search_symbols",
        {"query": q, "kinds": kinds, "limit": max(1, min(int(limit), 200))},
    )


@router.get("/codegraph/{code_project_id}/search/text")
def codegraph_search_text(
    code_project_id: str,
    q: str,
    limit: int = 20,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> list[dict[str, Any]]:
    # Die Volltextsuche ist ein Trigramm-Index: unter drei Zeichen gibt es
    # nichts zu suchen, und die Rust-Seite sagt das als Fehler. Hier vorher
    # abfangen, damit ein halbgetipptes Wort keine Fehlermeldung produziert.
    if len(q.strip()) < 3:
        return []
    return _query(
        code_project_id,
        metadata_db_path,
        "search_text",
        {"query": q, "limit": max(1, min(int(limit), 200))},
    )


@router.get("/codegraph/{code_project_id}/node/{node_id}")
def codegraph_node(
    code_project_id: str, node_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    detail = _query(code_project_id, metadata_db_path, "node", {"id": node_id})
    if detail is None:
        raise HTTPException(status_code=404, detail="Symbol nicht im Index")
    return detail


@router.get("/codegraph/{code_project_id}/blueprint/{node_id}")
def codegraph_blueprint(
    code_project_id: str, node_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Fokus-Symbol mit Aufrufern, Aufgerufenen und Kindern — je mit Beleg."""
    return _query(code_project_id, metadata_db_path, "blueprint", {"id": node_id})


@router.get("/codegraph/{code_project_id}/slice/{node_id}")
def codegraph_slice(
    code_project_id: str,
    node_id: str,
    depth: int = 1,
    direction: str = "both",
    budget: int = 400,
    edges: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Ausschnitt um ein Symbol, breitensuchend bis ``depth`` Sprünge.

    ``edges`` leer heißt hier **alle** Kantenarten — anders als bei
    ``/neighbours``, wo der Rust-Standard calls/reads/writes ist. Die Asymmetrie
    steckt in ``serve.rs`` (``edge_kinds(params, "edge_kinds", &[])`` gegen
    ``&REFERENCE_EDGES``) und ist beim Lesen der Antwort leicht zu übersehen.

    Zu ``direction=both``: ``Graph::neighbours`` hängt Aus- und Eingang flach
    aneinander, und ``Graph::slice`` trägt dann alles als *ausgehend* ein — in
    einer Liste unauffällig, auf einer Pfeilkarte eine Falschaussage. Wer Pfeile
    zeichnet, fragt zweimal (``out`` und ``in``) statt einmal ``both``.
    """
    return _query(
        code_project_id,
        metadata_db_path,
        "graph_slice",
        {
            "id": node_id,
            "depth": max(1, min(int(depth), 4)),
            "direction": _direction(direction),
            "budget": max(10, min(int(budget), 2000)),
            "edge_kinds": _kind_list(edges, ALL_EDGE_KINDS, "Kantenarten"),
        },
    )


@router.get("/codegraph/{code_project_id}/neighbours/{node_id}")
def codegraph_neighbours(
    code_project_id: str,
    node_id: str,
    direction: str = "both",
    edges: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> list[dict[str, Any]]:
    """Ein Sprung — über *alle* vierzehn Kantenarten, nicht nur die drei aus ``blueprint``.

    ``blueprint`` zeigt calls/reads/writes; wer wissen will, wer eine Datei
    importiert, welcher Test ein Symbol abdeckt, welche Tabelle es anfasst oder
    welcher Konfigurationsschlüssel es schaltet, kommt nur hier heran. ``edges``
    leer heißt der Rust-Standard calls/reads/writes, ``edges=all`` ist die
    ausdrückliche Vollauswahl.
    """
    return _query(
        code_project_id,
        metadata_db_path,
        "neighbours",
        {
            "id": node_id,
            "direction": _direction(direction),
            "edge_kinds": _kind_list(edges, ALL_EDGE_KINDS, "Kantenarten"),
        },
    )


@router.get("/codegraph/{code_project_id}/path")
def codegraph_path(
    code_project_id: str,
    from_id: str = Query(alias="from", min_length=1),
    to_id: str = Query(alias="to", min_length=1),
    max_depth: int = 12,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Kürzester **Aufruf**pfad zwischen zwei Symbolen.

    Die Rekursion in ``cs-graph`` verfolgt nur calls/reads/writes. ``path: null``
    heißt deshalb „kein Aufrufpfad" und nicht „keine Beziehung" — zwei Symbole
    können über Vererbung, Typen oder eine Route verbunden sein und hier trotzdem
    nichts liefern. Wer die Antwort anzeigt, muss das mitsagen.
    """
    return _query(
        code_project_id,
        metadata_db_path,
        "path_between",
        {"from": from_id, "to": to_id, "max_depth": max(1, min(int(max_depth), 24))},
    )


@router.get("/codegraph/{code_project_id}/impact/{node_id}")
def codegraph_impact(
    code_project_id: str,
    node_id: str,
    depth: int = 3,
    budget: int = 400,
    edges: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Auswirkungsanalyse: wer wird von einer Änderung an ``node_id`` mitrissen?

    Rückwärts-Erreichbarkeit (``impact`` in ``cs-graph``), mit Hop-Distanz je
    erreichtem Knoten und der schwächsten Sicherheitsstufe entlang des besten
    Pfades. Dazu die erreichenden **Tests** (über ``kind='test'``, nicht über die
    nie erzeugte ``tested_by``-Kante), die erreichenden **dynamischen Lücken**
    als ausdrückliche Grenze der statischen Analyse, und die betroffenen Dateien
    mit churn/risk.

    Ohne LLM voll nutzbar — das ist die Kernanforderung: die Änderung soll
    *vor* der Ausführung sichtbar sein, nicht erst im roten Test danach.
    ``edges`` leer fällt auf calls/reads/writes (REFERENCE_EDGES in ``serve.rs``).
    """
    return _query(
        code_project_id,
        metadata_db_path,
        "impact",
        {
            "id": node_id,
            "max_depth": max(1, min(int(depth), 8)),
            "budget": max(10, min(int(budget), 2000)),
            "edge_kinds": _kind_list(edges, ALL_EDGE_KINDS, "Kantenarten"),
        },
    )


@router.get("/codegraph/{code_project_id}/top")
def codegraph_top_symbols(
    code_project_id: str,
    kind: str | None = None,
    limit: int = 30,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> list[dict[str, Any]]:
    """Die wichtigsten Symbole je Art, nach Relevanz.

    ``overview`` hat Funktionen/Methoden/Klassen fest verdrahtet. Hier sind auch
    die anderen zwölf Arten erreichbar — Routen, Tabellen, Tests,
    Konfigurationsschlüssel, dynamische Lücken —, ohne die man ein fremdes
    Projekt nur durch die Brille seiner Funktionen sieht.
    """
    return _query(
        code_project_id,
        metadata_db_path,
        "top_symbols",
        {
            "kinds": _kind_list(kind, ALL_NODE_KINDS, "Symbolarten"),
            "limit": max(1, min(int(limit), 200)),
        },
    )


# --- Die Bereiche (Cluster-Landkarte) ----------------------------------------


@router.get("/codegraph/{code_project_id}/clusters")
def codegraph_clusters(
    code_project_id: str,
    prefix: str = "",
    edges: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Woraus besteht dieses Projekt — eine Ebene auf einmal.

    ``prefix`` ist leer für die oberste Ebene und geht dann Pfadsegment für
    Pfadsegment tiefer (``query`` → ``query/retrieval`` → …). Die Struktur ist
    ein Ordner-Rollup über den Index, also nachprüfbar; die aufsummierten Kanten
    tragen ihre Anzahl **und die schwächste** enthaltene Sicherheitsstufe. Wer
    den Beleg dazu will, holt ihn über ``…/clusters/edge``.

    Ohne ``edges`` gilt der Standardsatz der Rust-Seite (calls, reads, writes,
    imports, inherits, implements) — ``contains`` ist draussen, sonst hinge jeder
    Bereich über seine eigenen Dateien an sich selbst.
    """
    from codegraph import clusters as cluster_service

    project = _load_project(code_project_id, metadata_db_path)
    kinds = _kind_list(edges, ALL_EDGE_KINDS, "Kantenarten")
    try:
        payload = cluster_service.level(project, prefix=prefix, edge_kinds=kinds)
    except CodeSearchMissingError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except CodeGraphError as error:
        raise HTTPException(status_code=502, detail=f"Code-Graph: {error}") from error

    with MetadataDB(metadata_db_path) as db:
        stored = db.get_cluster_labels(code_project_id)
    return cluster_service.apply_labels(payload, stored)


@router.get("/codegraph/{code_project_id}/clusters/edge")
def codegraph_cluster_edge(
    code_project_id: str,
    from_: str = Query(alias="from"),
    to: str = Query(...),
    edges: str | None = None,
    limit: int = 60,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> list[dict[str, Any]]:
    """Die echten Kanten hinter einer aufsummierten Bereichs-Kante.

    Das ist der Beleg zur Zahl auf dem Pfeil. Ohne ihn wäre die Landkarte genau
    die Art unüberprüfbare Zusammenfassung, gegen die der Rest gebaut ist:
    „128 Aufrufe" liesse sich nicht nachsehen.
    """
    return _query(
        code_project_id,
        metadata_db_path,
        "cluster_edges",
        {
            "from": from_,
            "to": to,
            "edge_kinds": _kind_list(edges, ALL_EDGE_KINDS, "Kantenarten"),
            "limit": max(1, min(int(limit), 400)),
        },
    )


@router.get("/codegraph/{code_project_id}/clusters/members")
def codegraph_cluster_members(
    code_project_id: str,
    prefix: str = "",
    limit: int = 30,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> list[dict[str, Any]]:
    """Die wichtigsten Symbole eines Bereichs — der Übergang zur Symbolkarte."""
    return _query(
        code_project_id,
        metadata_db_path,
        "cluster_members",
        {"prefix": prefix, "limit": max(1, min(int(limit), 200))},
    )


@router.post("/codegraph/{code_project_id}/clusters/name")
async def codegraph_name_clusters(
    code_project_id: str, request: ClusterNameRequest
) -> StreamingResponse:
    """Eine Ebene benennen lassen — SSE.

    Das Modell sieht nur Kennzahlen, keinen Quelltext, und es verändert nichts
    an der Struktur. Fällt es aus, bleibt die Karte vollständig und trägt eben
    Ordnernamen.
    """
    from codegraph import clusters as cluster_service

    def events() -> Iterator[dict[str, Any]]:
        with MetadataDB(request.metadata_db_path) as db:
            project = _require_code_project(db, code_project_id)
            yield from cluster_service.name_level_stream(
                project,
                prefix=request.prefix,
                code_project_id=code_project_id,
                db=db,
                provider=request.provider,
                model=request.model,
                force=request.force,
            )

    return _sse(events)


@router.get("/codegraph/{code_project_id}/diagram/{node_id}")
def codegraph_diagram(
    code_project_id: str,
    node_id: str,
    kind: str = "class",
    depth: int = 2,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Klassen- oder Sequenzdiagramm um ein Symbol.

    Beide Formen tragen an jeder Kante ihre Sicherheitsstufe und Belegstelle. Ein
    Diagramm sieht autoritativer aus als jede Textzeile — würde die Stufe hier
    wegfallen, würde aus einer über Namensgleichheit geratenen Kante eine
    gezeichnete Tatsache.
    """
    if kind not in {"class", "sequence"}:
        raise HTTPException(
            status_code=400, detail="kind muss 'class' oder 'sequence' sein"
        )
    method = "class_diagram" if kind == "class" else "sequence_diagram"
    params: dict[str, Any] = {"id": node_id}
    if kind == "sequence":
        params["depth"] = max(1, min(int(depth), 4))
    return _query(code_project_id, metadata_db_path, method, params)


@router.get("/codegraph/{code_project_id}/source")
def codegraph_source(
    code_project_id: str, path: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Dateiinhalt, geprüft gegen den Hash von der Indizierung.

    ``stale`` heißt: die Datei hat sich seither geändert, die Zeilennummern aus
    dem Graphen passen nicht mehr. Das wird gesagt, statt es zu verschweigen.
    """
    return _query(code_project_id, metadata_db_path, "source", {"path": path})


def _symbol_source(
    code_project_id: str, node_id: str, metadata_db_path: str
) -> dict[str, Any]:
    """Zeilenbereich, Text und Zustand einer einzelnen Funktion.

    Gelesen wird über ``source``, nicht direkt von der Platte: nur dieser Weg
    vergleicht den beim Indizieren aufgezeichneten Hash und sagt, ob die
    Zeilennummern aus dem Graphen überhaupt noch dorthin zeigen, wo sie hin
    zeigten.
    """
    node = _query(code_project_id, metadata_db_path, "node", {"id": node_id})
    if not node:
        raise HTTPException(status_code=404, detail="Symbol nicht im Index")

    span = node.get("span") or {}
    start_line = int(span.get("start_line") or 0)
    end_line = int(span.get("end_line") or start_line)
    if start_line < 1:
        raise HTTPException(status_code=409, detail="Symbol ohne Zeilenbereich")

    source = _query(code_project_id, metadata_db_path, "source", {"path": node["path"]})
    lines = str(source.get("text") or "").splitlines()
    stale = bool(source.get("stale")) or end_line > len(lines)
    end_line = min(max(end_line, start_line), max(len(lines), start_line))

    return {
        "node_id": node_id,
        "qualified": node.get("qualified"),
        "lang": node.get("lang"),
        "path": node["path"],
        "start_line": start_line,
        "end_line": end_line,
        "text": "\n".join(lines[start_line - 1 : end_line]),
        "stale": stale,
    }


@router.get("/codegraph/{code_project_id}/symbol/{node_id}/source")
def codegraph_symbol_source(
    code_project_id: str, node_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Nur die Zeilen einer Funktion — zum Lesen und Ändern an Ort und Stelle.

    ``stale: true`` heisst: die Datei hat sich seit dem Indizieren geändert, der
    Zeilenbereich ist nicht mehr verlässlich. Dann wird angezeigt, aber nicht
    geschrieben — sonst landete die Änderung an einer Stelle, die niemand
    gesehen hat.
    """
    project = _load_project(code_project_id, metadata_db_path)
    root = workspace_manager.ensure_exists(project)
    body = _symbol_source(code_project_id, node_id, metadata_db_path)
    return {**body, "content_hash": _file_hash(root, body["path"])}


@router.patch("/codegraph/{code_project_id}/symbol/{node_id}/source")
def codegraph_write_symbol_source(
    code_project_id: str, node_id: str, request: SymbolSourceRequest
) -> dict[str, Any]:
    """Genau die Zeilen dieser Funktion zurückschreiben.

    Zwei Sperren, und beide melden **409**:

    * Der ``content_hash`` passt nicht mehr → jemand anders (oder ein anderer
      Tab) hat die Datei inzwischen geschrieben. Stilles Überschreiben wäre
      Datenverlust ohne Spur.
    * Der Index ist veraltet → der Zeilenbereich aus dem Graphen zeigt nicht
      mehr auf diese Funktion. Erst neu indizieren.
    """
    project = _load_project(code_project_id, request.metadata_db_path)
    root = workspace_manager.ensure_exists(project)
    body = _symbol_source(code_project_id, node_id, request.metadata_db_path)

    if body["stale"]:
        raise HTTPException(
            status_code=409,
            detail="Der Index kennt einen älteren Stand dieser Datei. Bitte erst neu indizieren.",
        )
    if _file_hash(root, body["path"]) != request.content_hash:
        raise HTTPException(
            status_code=409,
            detail="Die Datei wurde inzwischen anderswo geändert. Bitte neu laden.",
        )

    path = workspace_manager.resolve_within(root, body["path"], must_exist=True)
    original = path.read_text(encoding="utf-8", errors="replace")
    updated = _splice_lines(
        original, body["start_line"], body["end_line"], request.text
    )
    # Vor dem Schreiben sichern — fail-soft, blockiert niemals. Ohne git gibt es
    # eben keinen RÜckweg; das steht in der Antwort, nicht im Kleingedruckten.
    with MetadataDB(request.metadata_db_path) as db:
        checkpoint = workspace_checkpoints.auto_checkpoint(
            db,
            code_project_id,
            root,
            reason="auto_symbol_write",
            label=f"vor Symbol-Schreibung: {body['path']}",
        )
    workspace_manager.write_file(root, body["path"], updated)

    return {
        "node_id": node_id,
        "path": body["path"],
        "start_line": body["start_line"],
        "end_line": body["start_line"] + request.text.count("\n"),
        "written": True,
        "content_hash": _file_hash(root, body["path"]),
        # Ab jetzt zeigen die Zeilennummern im Graphen woanders hin. Das wird
        # gesagt, nicht verschwiegen.
        "index_stale": True,
        "checkpoint": checkpoint,
    }


@router.post("/codegraph/{code_project_id}/positions")
def codegraph_positions(
    code_project_id: str, request: PositionsRequest
) -> list[dict[str, Any]]:
    """``datei:zeile`` aus Terminal-Ausgabe, aufgelöst auf Symbole."""
    project = _load_project(code_project_id, request.metadata_db_path)
    try:
        return service.resolve_positions(project, request.text, limit=request.limit)
    except workspace_manager.WorkspaceError:
        raise
    except CodeGraphError as error:
        raise HTTPException(status_code=502, detail=f"Code-Graph: {error}") from error


# --- Der Begleiter -----------------------------------------------------------


@router.post("/codegraph/{code_project_id}/explain/{node_id}")
async def codegraph_explain(
    code_project_id: str, node_id: str, request: ExplainRequest
) -> StreamingResponse:
    """Ein Symbol erklären lassen — als SSE, mit geprüften Belegen.

    Getrennt von ``/ask``, weil hier keine Suche nötig ist: das Symbol steht
    fest, und ``get_node`` liefert Fakten und Quelltext in einem. Genau dieses
    Nachschlagen ist die Lizenz zum Zitieren — was das Modell nicht bekommen
    hat, darf es auch nicht belegen.

    Wird **nicht** in ``code_answers`` mitgeschrieben: eine Erklärung ist an den
    Stand einer Datei gebunden und veraltet mit der nächsten Änderung; eine
    Historie davon hätte kurze Haltbarkeit und würde die Fragen-Historie
    verwässern.
    """
    project = _load_project(code_project_id, request.metadata_db_path)

    def events() -> Iterator[dict[str, Any]]:
        from codegraph import explain as explain_module

        yield from explain_module.explain_stream(
            project,
            node_id,
            provider=request.provider,
            model=request.model,
        )

    return _sse(events)


@router.get("/codegraph/{code_project_id}/why/{node_id}")
def codegraph_recorded_rationale(
    code_project_id: str, node_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Die **aufgezeichnete** Hälfte von „warum wurde das so gebaut".

    Getrennt vom ``POST``, und zwar nicht aus Bequemlichkeit: was in git steht
    und was jemand hinterlegt hat, ist Beleg und braucht kein Modell. Es muss
    auch dann dastehen, wenn kein LLM erreichbar ist — und es darf nie in
    demselben Block landen wie eine Herleitung.
    """
    from codegraph import rationale as rationale_module

    project = _load_project(code_project_id, metadata_db_path)
    detail = _query(code_project_id, metadata_db_path, "node", {"id": node_id})
    if not detail:
        raise HTTPException(status_code=404, detail="Symbol nicht im Index")

    span = detail.get("span") or {}
    rel_path = str(detail.get("path") or "")
    start_line = int(span.get("start_line") or 1)
    end_line = int(span.get("end_line") or start_line)

    root = workspace_manager.ensure_exists(project)
    try:
        current_hash: str | None = _file_hash(root, rel_path)
    except Exception:  # noqa: BLE001 — Datei weg ist kein Grund für eine 500
        current_hash = None

    with MetadataDB(metadata_db_path) as db:
        facts = rationale_module.recorded(
            project,
            rel_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            code_project_id=code_project_id,
            db=db,
            symbol_id=node_id,
        )

    # Wie bei den Code-Zitaten: hat sich die Datei geändert, gilt die Begründung
    # als überholt, statt weiter als Aussage über Code zu stehen, den sie nie
    # gesehen hat.
    notes = [
        {
            **note,
            "stale": bool(
                note.get("content_hash") and note["content_hash"] != current_hash
            ),
        }
        for note in facts["notes"]
    ]
    return {
        "node_id": node_id,
        "path": rel_path,
        "start_line": start_line,
        "end_line": end_line,
        "history": facts["history"],
        "notes": notes,
    }


@router.post("/codegraph/{code_project_id}/why/{node_id}")
async def codegraph_why(
    code_project_id: str, node_id: str, request: ExplainRequest
) -> StreamingResponse:
    """Die **hergeleitete** Hälfte, als SSE.

    Nicht persistiert: eine Herleitung veraltet mit der nächsten Änderung, genau
    wie ``explain``. Wer etwas festhalten will, schreibt es nach
    ``/rationale`` — und dann steht es unter *Aufgezeichnet*, wo es hingehört.
    """
    project = _load_project(code_project_id, request.metadata_db_path)

    def events() -> Iterator[dict[str, Any]]:
        from codegraph import rationale as rationale_module

        with MetadataDB(request.metadata_db_path) as db:
            yield from rationale_module.why_stream(
                project,
                node_id,
                code_project_id=code_project_id,
                db=db,
                provider=request.provider,
                model=request.model,
            )

    return _sse(events)


@router.get("/codegraph/{code_project_id}/rationale")
def codegraph_list_rationale(
    code_project_id: str,
    rel_path: str | None = None,
    symbol_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Hinterlegte Begründungen, neueste zuerst — mit Veraltungs-Merker."""
    project = _load_project(code_project_id, metadata_db_path)
    root = workspace_manager.ensure_exists(project)
    with MetadataDB(metadata_db_path) as db:
        notes = db.list_code_rationale(
            code_project_id, rel_path=rel_path, symbol_id=symbol_id, limit=limit
        )

    hashes: dict[str, str | None] = {}
    entries = []
    for note in notes:
        path = str(note.get("rel_path") or "")
        if path not in hashes:
            try:
                hashes[path] = _file_hash(root, path)
            except Exception:  # noqa: BLE001 — gelöschte Datei ist kein Fehler
                hashes[path] = None
        stale = bool(note.get("content_hash") and note["content_hash"] != hashes[path])
        entries.append({**note, "stale": stale})
    return {"code_project_id": code_project_id, "rationale": entries}


@router.post("/codegraph/{code_project_id}/rationale")
def codegraph_add_rationale(
    code_project_id: str, request: RationaleRequest
) -> dict[str, Any]:
    """Eine eigene Begründung festhalten.

    Das ist der Teil, der das Problem langfristig löst: der Prompt einer
    erzeugenden KI ist nirgends aufgezeichnet, aber ab hier gibt es eine
    Aufzeichnung. Der ``content_hash`` wird beim Schreiben genommen, damit
    später sichtbar wird, wenn sich der begründete Code geändert hat.
    """
    project = _load_project(code_project_id, request.metadata_db_path)
    root = workspace_manager.ensure_exists(project)

    rel_path = request.rel_path
    start_line = request.start_line
    end_line = request.end_line
    if request.symbol_id and not rel_path:
        detail = _query(
            code_project_id, request.metadata_db_path, "node", {"id": request.symbol_id}
        )
        if not detail:
            raise HTTPException(status_code=404, detail="Symbol nicht im Index")
        span = detail.get("span") or {}
        rel_path = str(detail.get("path") or "")
        start_line = start_line or int(span.get("start_line") or 1)
        end_line = end_line or int(span.get("end_line") or start_line or 1)
    if not rel_path:
        raise HTTPException(status_code=400, detail="rel_path oder symbol_id ist nötig")

    with MetadataDB(request.metadata_db_path) as db:
        saved = db.add_code_rationale(
            code_project_id,
            rel_path=rel_path,
            text=request.text,
            start_line=start_line,
            end_line=end_line,
            symbol_id=request.symbol_id,
            content_hash=_file_hash(root, rel_path),
            author=request.author,
        )
    return {**(saved or {}), "stale": False}


@router.delete("/codegraph/{code_project_id}/rationale/{rationale_id}")
def codegraph_delete_rationale(
    code_project_id: str,
    rationale_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        if not db.delete_code_rationale(rationale_id):
            raise HTTPException(status_code=404, detail="Begründung nicht gefunden")
    return {"id": rationale_id, "deleted": True}


@router.post("/codegraph/{code_project_id}/ask")
async def codegraph_ask(code_project_id: str, request: AskRequest) -> StreamingResponse:
    """Frage → Aktivitätszeilen → geprüfte Antwort, als SSE.

    Der Strom ist nicht Kosmetik: beim Zusehen, *wo* der Begleiter nachschlägt,
    entsteht das Vertrauen in die Antwort. Ein Spinner sagt „warte", die Zeilen
    sagen „ich habe hier nachgesehen" — und der Spur-Block am Ende sagt, wo man
    es selbst nachprüfen kann.

    Die Antwort wird mitgeschrieben (``code_answers``), samt Urteil: eine
    Q&A-Historie, in der man später sieht, welche Antwort auf welchem Stand des
    Codes beruhte.
    """
    project = _load_project(code_project_id, request.metadata_db_path)

    def events() -> Iterator[dict[str, Any]]:
        from codegraph import companion

        for event in companion.ask_stream(
            project,
            request.question,
            session=request.session,
            provider=request.provider,
            model=request.model,
            research_project_id=request.project_id,
            paper_ids=request.paper_ids,
            use_papers=request.use_papers,
            metadata_db_path=request.metadata_db_path,
        ):
            if event.get("event") == "done":
                answer = event.get("answer") or {}
                # Erst speichern, dann ausliefern — und ein Fehler beim Speichern
                # darf die fertige Antwort nicht verschlucken.
                try:
                    with MetadataDB(request.metadata_db_path) as db:
                        record = db.add_code_answer(
                            {
                                "code_project_id": code_project_id,
                                "project_id": request.project_id,
                                "question": answer.get("question"),
                                "answer": answer.get("answer"),
                                "citations": answer.get("citations"),
                                "trail": answer.get("trail"),
                                "verdict": answer.get("verdict"),
                                "tool_calls": answer.get("tool_calls"),
                                "provider": answer.get("provider"),
                                "model": answer.get("model"),
                            }
                        )
                    answer = {**answer, "id": (record or {}).get("id")}
                except Exception as error:  # noqa: BLE001 — Historie ist Zugabe
                    answer = {**answer, "id": None, "persist_error": str(error)}
                yield {"event": "done", "answer": answer}
                continue
            yield event

    return _sse(events)


# --- Der Chat (mehrere Züge, mit Trefferliste) --------------------------------


@router.post("/codegraph/{code_project_id}/chats")
def codegraph_create_chat(
    code_project_id: str, request: ChatCreateRequest
) -> dict[str, Any]:
    """Ein neues Gespräch anlegen.

    Die Sitzungskennung entsteht hier und bleibt für das ganze Gespräch: an ihr
    hängt auf der Rust-Seite die Lizenz zum Zitieren. Ein Gespräch = eine Lizenz.
    """
    with MetadataDB(request.metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        chat = db.create_code_chat(
            code_project_id, project_id=request.project_id, title=request.title
        )
    if chat is None:
        raise HTTPException(
            status_code=500, detail="Gespräch konnte nicht angelegt werden"
        )
    return chat


@router.get("/codegraph/{code_project_id}/chats")
def codegraph_list_chats(
    code_project_id: str,
    limit: int = 50,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        chats = db.list_code_chats(code_project_id, limit=max(1, min(int(limit), 200)))
    return {"code_project_id": code_project_id, "chats": chats}


@router.get("/codegraph/{code_project_id}/chats/{chat_id}")
def codegraph_get_chat(
    code_project_id: str, chat_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        chat = db.get_code_chat(chat_id)
        if chat is None or str(chat.get("code_project_id")) != code_project_id:
            raise HTTPException(status_code=404, detail="Gespräch nicht gefunden")
        turns = db.list_code_chat_turns(chat_id)
    return {"chat": chat, "turns": turns}


@router.delete("/codegraph/{code_project_id}/chats/{chat_id}")
def codegraph_delete_chat(
    code_project_id: str, chat_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        chat = db.get_code_chat(chat_id)
        if chat is None or str(chat.get("code_project_id")) != code_project_id:
            raise HTTPException(status_code=404, detail="Gespräch nicht gefunden")
        deleted = db.delete_code_chat(chat_id)
    return {"id": chat_id, "deleted": deleted}


@router.post("/codegraph/{code_project_id}/chats/{chat_id}/ask")
async def codegraph_chat_ask(
    code_project_id: str, chat_id: str, request: ChatAskRequest
) -> StreamingResponse:
    """Ein Zug im Gespräch — SSE, wie ``/ask``, aber mit Verlauf und Trefferliste.

    Zwei Unterschiede zu ``/ask``, beide absichtlich:

    * Der bisherige Verlauf geht gekürzt in den Prompt, und die Sitzung wird
      **erweitert** statt ersetzt — eine Rückfrage darf zitieren, was in Runde
      eins gezeigt wurde.
    * Die Vorab-Suche läuft breit (Namens- *und* Volltextsuche, mehr Symbole),
      weil hier Bereichsfragen gestellt werden: „welche Funktionen gehören zu
      diesem Feature" ist mit sechs Symbolen nicht zu beantworten.
    """
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, code_project_id)
        chat = db.get_code_chat(chat_id)
        if chat is None or str(chat.get("code_project_id")) != code_project_id:
            raise HTTPException(status_code=404, detail="Gespräch nicht gefunden")
        previous = db.list_code_chat_turns(chat_id)

    session_key = str(chat.get("session_key") or chat_id)
    history = [
        {"question": turn.get("question") or "", "answer": turn.get("answer") or ""}
        for turn in previous
    ]

    def events() -> Iterator[dict[str, Any]]:
        from codegraph import companion

        for event in companion.ask_stream(
            project,
            request.question,
            session=session_key,
            provider=request.provider,
            model=request.model,
            research_project_id=request.project_id,
            paper_ids=request.paper_ids,
            use_papers=request.use_papers,
            history=history,
            broad=True,
            max_symbols=18,
            metadata_db_path=request.metadata_db_path,
        ):
            if event.get("event") != "done":
                yield event
                continue

            answer = event.get("answer") or {}
            try:
                with MetadataDB(request.metadata_db_path) as db:
                    record = db.add_code_chat_turn(
                        chat_id,
                        {
                            **answer,
                            "remote_model": _is_remote_model(answer.get("model")),
                        },
                    )
                answer = {
                    **answer,
                    "id": (record or {}).get("id"),
                    "ordinal": (record or {}).get("ordinal"),
                    "remote_model": _is_remote_model(answer.get("model")),
                }
            except Exception as error:  # noqa: BLE001 — Historie ist Zugabe
                answer = {**answer, "id": None, "persist_error": str(error)}
            yield {"event": "done", "answer": answer}

    return _sse(events)


@router.get("/codegraph/{code_project_id}/answers")
def codegraph_answers(
    code_project_id: str,
    limit: int = 30,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Bisherige Fragen und ihre geprüften Antworten, neueste zuerst."""
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        answers = db.list_code_answers(
            code_project_id, limit=max(1, min(int(limit), 200))
        )
    return {"code_project_id": code_project_id, "answers": answers}


@router.delete("/codegraph/{code_project_id}/answers/{answer_id}")
def codegraph_delete_answer(
    code_project_id: str,
    answer_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        deleted = db.delete_code_answer(answer_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Antwort nicht gefunden")
    return {"id": answer_id, "deleted": True}


# --- Code-Zitate in Notizen --------------------------------------------------


def _file_hash(root: Any, rel_path: str) -> str:
    """Zustand einer Datei beim Zitieren, als Hash.

    Nur damit lässt sich später sagen, dass eine Zeilennummer nicht mehr
    dorthin zeigt, wo sie hinzeigte, als jemand sie aufgeschrieben hat.
    """
    path = workspace_manager.resolve_within(root, rel_path, must_exist=True)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.post("/codegraph/{code_project_id}/cite")
def codegraph_cite(code_project_id: str, request: CiteRequest) -> dict[str, Any]:
    """Zeilen aus dem Code als Zitat in eine Notiz übernehmen.

    Keine zweite Tabelle: das landet in ``note_citations`` neben den
    Paper-Zitaten. ``paper_id`` bekommt die synthetische ID
    ``code:<projekt>:<pfad>:<zeile>``, damit alle bestehenden Leser der Tabelle
    unverändert weiterlaufen.
    """
    project = _load_project(code_project_id, request.metadata_db_path)
    root = workspace_manager.ensure_exists(project)
    end_line = max(request.end_line or request.start_line, request.start_line)

    excerpt = request.reference_text
    if excerpt is None:
        # Den Ausschnitt aus der Datei holen, statt ihn dem Aufrufer zu glauben:
        # ein Zitat, das niemand aus der Datei gelesen hat, ist keins.
        path = workspace_manager.resolve_within(root, request.rel_path, must_exist=True)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        excerpt = "\n".join(lines[request.start_line - 1 : end_line])

    span = (
        f"{request.start_line}"
        if end_line == request.start_line
        else f"{request.start_line}-{end_line}"
    )
    citation = {
        "source_kind": "code",
        "paper_id": f"code:{code_project_id}:{request.rel_path}:{span}",
        "title": request.title or f"{request.rel_path}:{span}",
        "kind": "code",
        "reference_text": excerpt,
        "code_project_id": code_project_id,
        "rel_path": request.rel_path,
        "start_line": request.start_line,
        "end_line": end_line,
        "content_hash": _file_hash(root, request.rel_path),
    }

    with MetadataDB(request.metadata_db_path) as db:
        if db.get_note(request.note_id) is None:
            raise HTTPException(status_code=404, detail="Notiz nicht gefunden")
        saved = db.add_note_citation(request.note_id, citation)
    return {**saved, "stale": False}


@router.get("/codegraph/{code_project_id}/citations")
def codegraph_citations(
    code_project_id: str,
    note_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Die Code-Zitate einer Notiz — jedes mit der Frage, ob es noch gilt.

    ``stale`` heißt: die Datei hat sich seit dem Zitieren geändert, die
    Zeilennummern zeigen nicht mehr an dieselbe Stelle. Das wird gesagt, statt
    eine alte Zeilennummer stillschweigend als aktuell auszugeben.
    """
    project = _load_project(code_project_id, metadata_db_path)
    root = workspace_manager.ensure_exists(project)

    with MetadataDB(metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail="Notiz nicht gefunden")
        rows = db.list_note_citations(note_id)

    citations: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("source_kind") or "paper") != "code":
            continue
        if str(row.get("code_project_id") or "") != code_project_id:
            continue
        try:
            current = _file_hash(root, str(row.get("rel_path") or ""))
            stale = current != str(row.get("content_hash") or "")
            missing = False
        except workspace_manager.WorkspaceError:
            # Datei weg: das Zitat gilt erst recht nicht mehr, ist aber kein Fehler.
            stale, missing = True, True
        citations.append({**row, "stale": stale, "missing": missing})
    return {
        "code_project_id": code_project_id,
        "note_id": note_id,
        "citations": citations,
    }


# --- Paper ↔ Code ------------------------------------------------------------


@router.get("/codegraph/{code_project_id}/links")
def codegraph_list_links(
    code_project_id: str,
    project_id: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Womit ist dieser Code verknüpft — welches Paper beschreibt was hier steht?"""
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        links = db.list_code_paper_links(code_project_id, project_id)
    return {"code_project_id": code_project_id, "links": links}


@router.post("/codegraph/{code_project_id}/links")
def codegraph_add_link(code_project_id: str, request: LinkRequest) -> dict[str, Any]:
    if not request.paper_id and not request.symbol_id and not request.rel_path:
        raise HTTPException(
            status_code=400,
            detail="Eine Verknüpfung braucht mindestens ein Paper, ein Symbol oder eine Datei",
        )
    with MetadataDB(request.metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        link = db.add_code_paper_link(
            code_project_id,
            project_id=request.project_id,
            paper_id=request.paper_id,
            symbol_id=request.symbol_id,
            rel_path=request.rel_path,
            start_line=request.start_line,
            end_line=request.end_line,
            kind=request.kind,
            note=request.note,
        )
    return link or {}


@router.delete("/codegraph/{code_project_id}/links/{link_id}")
def codegraph_delete_link(
    code_project_id: str, link_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, code_project_id)
        deleted = db.delete_code_paper_link(link_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Verknüpfung nicht gefunden")
    return {"id": link_id, "deleted": True}


# --------------------------------------------------------------------------- #
# Spaghetti-Löser (Stufe 2): Diagnose ohne LLM, Vorschlag mit LLM, geprüft      #
# --------------------------------------------------------------------------- #


@router.get("/codegraph/{code_project_id}/hotspots")
def codegraph_hotspots(
    code_project_id: str,
    loc: int = 200,
    complexity: int = 15,
    max_nesting: int = 5,
    fan_in: int = 30,
    fan_out: int = 25,
    churn: int = 0,
    limit: int = 50,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> list[dict[str, Any]]:
    """Spaghetti-Hotspots — Symbole, die eine gemessene Regel reißen.

    Jede Fundstelle trägt, *welche* Regel ausgelöst wurde und mit welchem
    Messwert — keine erfundene Gesamtnote. ``churn=0`` fällt auf das obere
    Dezil dieses Index (relativ, nicht konfiguriert). Schwellen ``0``
    deaktivieren die jeweilige Regel. Ohne LLM voll nutzbar.
    """
    return _query(
        code_project_id,
        metadata_db_path,
        "hotspots",
        {
            "loc": max(0, int(loc)),
            "complexity": max(0, int(complexity)),
            "max_nesting": max(0, int(max_nesting)),
            "fan_in": max(0, int(fan_in)),
            "fan_out": max(0, int(fan_out)),
            "churn": max(0, int(churn)),
            "limit": max(1, min(int(limit), 500)),
        },
    )


@router.get("/codegraph/{code_project_id}/cycles")
def codegraph_cycles(
    code_project_id: str,
    level: str = "file",
    edges: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    """Ringe im Abhängigkeitsgraph — Tarjan-SCC, mit Belegkanten.

    ``level=file`` (Standard) läuft über ``imports`` und antwortet auf „welche
    Module lassen sich nicht trennen"; ``level=symbol`` über ``calls`` und
    antwortet auf „wer ruft wen im Kreis". Jeder Ring trägt seine Belegkanten
    und die schwächste Sicherheitsstufe — eine geratene Kante im Ring macht den
    Ring zur Vermutung. Ohne LLM voll nutzbar.
    """
    if level not in ("file", "symbol"):
        raise HTTPException(
            status_code=400, detail="level muss 'file' oder 'symbol' sein"
        )
    cycles = _query(
        code_project_id,
        metadata_db_path,
        "cycles",
        {
            "level": level,
            "edge_kinds": _kind_list(edges, ALL_EDGE_KINDS, "Kantenarten"),
        },
    )
    return {"code_project_id": code_project_id, "level": level, "cycles": cycles}


class RefactorProposeRequest(BaseModel):
    """Bitte um einen Refactor-Vorschlag zu einem Symbol."""

    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class RefactorTryRequest(BaseModel):
    """Einen geprüften Vorschlag in einer Sandbox anwenden und testen.

    ``sandbox_id`` fehlt ⇒ es wird eine neue Sandbox auf dem neuesten Checkpoint
    angelegt. ``test_command`` überschreibt den erkannten Befehl.
    """

    proposal: dict[str, Any]
    sandbox_id: str | None = None
    test_command: str | None = None
    timeout: int = 300
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


@router.post("/codegraph/{code_project_id}/refactor/{node_id}")
async def codegraph_refactor_propose(
    code_project_id: str, node_id: str, request: RefactorProposeRequest
) -> StreamingResponse:
    """Refactor-Vorschlag als SSE — geprüft, nicht geglaubt.

    Liefert ``get_node`` + Quelltext dem Modell, verlangt JSON mit vollständigem
    neuen Dateiinhalt, und prüft Pfade (``resolve_within``) sowie Python-Syntax
    (``compile``), bevor der Vorschlag durchgeht. Ein Vorschlag außerhalb des
    Projekts wird verworfen, nicht korrigiert. Ohne Modell: ``failed``-Ereignis
    wie überall im Code-Graph; die Diagnose (``/hotspots``, ``/cycles``) steht
    trotzdem.
    """
    project = _load_project(code_project_id, request.metadata_db_path)

    def events() -> Iterator[dict[str, Any]]:
        from codegraph import refactor as refactor_module

        yield from refactor_module.propose_stream(
            project,
            node_id,
            provider=request.provider,
            model=request.model,
        )

    return _sse(events)


@router.post("/codegraph/{code_project_id}/refactor/{node_id}/try")
async def codegraph_refactor_try(
    code_project_id: str, node_id: str, request: RefactorTryRequest
) -> StreamingResponse:
    """Vorschlag in einer Sandbox anwenden und den Testbefehl laufen lassen — SSE.

    Ablauf: Sandbox (neu auf dem neuesten Checkpoint oder die angegebene) →
    Vorschlag in den Worktree schreiben → Testbefehl → Diff + Ausgabe. Die
    Übernahme in den Hauptbaum ist ein eigener Schritt (``/sandboxes/{id}/apply``),
    nie automatisch. Ein Vorschlag, der die :func:`validate_proposal`-Prüfung
    nicht bestanden hat, wird nicht einmal geschrieben.
    """
    from codegraph import refactor as refactor_module

    project = _load_project(code_project_id, request.metadata_db_path)
    root = workspace_manager.ensure_exists(project)

    def events() -> Iterator[dict[str, Any]]:
        # 1. Vorschlag prüfen (noch ohne zu schreiben).
        ok, errors, cleaned = refactor_module.validate_proposal(root, request.proposal)
        if not ok:
            yield {"event": "failed", "error": "Vorschlag ungültig", "errors": errors}
            return

        # 2. Sandbox beschaffen: die angegebene oder eine neue auf dem neuesten
        # Checkpoint. Ohne Checkpoint kein Probelauf — fail-soft, nicht 500.
        with MetadataDB(request.metadata_db_path) as db:
            if request.sandbox_id:
                sandbox = db.get_code_sandbox(request.sandbox_id)
            else:
                checkpoints = db.list_code_checkpoints(code_project_id)
                sandbox = None
                checkpoint = checkpoints[0] if checkpoints else None
        if request.sandbox_id and sandbox is None:
            yield {"event": "failed", "error": "Sandbox nicht gefunden"}
            return
        if sandbox is None:
            if checkpoint is None:
                yield {
                    "event": "failed",
                    "error": "Kein Checkpoint vorhanden — lege zuerst einen an",
                }
                return
            with MetadataDB(request.metadata_db_path) as db:
                record = db.add_code_sandbox(
                    code_project_id,
                    checkpoint_id=checkpoint["id"],
                    base_sha=checkpoint["commit_sha"],
                    status="creating",
                    test_command=request.test_command,
                )
            created = workspace_sandbox.create_worktree(
                root, checkpoint["commit_sha"], record["id"]
            )
            with MetadataDB(request.metadata_db_path) as db:
                if created.get("created"):
                    db.update_code_sandbox(
                        record["id"],
                        status="created",
                        test_command=request.test_command,
                    )
                    sandbox = db.get_code_sandbox(record["id"]) or record
                    sandbox["path"] = created["path"]
                else:
                    db.update_code_sandbox(record["id"], status="failed")
                    yield {
                        "event": "failed",
                        "error": created.get("error")
                        or created.get("reason")
                        or "Sandbox nicht anlegbar",
                    }
                    return
        worktree = Path(sandbox["path"])
        if not worktree.is_dir():
            yield {
                "event": "failed",
                "error": "Sandbox-Verzeichnis nicht mehr vorhanden",
            }
            return

        yield {"event": "activity", "text": "schreibe den Vorschlag in die Sandbox"}
        applied = refactor_module.apply_to_worktree(worktree, cleaned)
        yield {"event": "applied", "applied": applied}

        # 3. Testbefehl: explizit, sonst hinterlegt, sonst erkannt.
        if request.test_command:
            import shlex

            command = shlex.split(request.test_command)
        elif sandbox.get("test_command"):
            import shlex

            command = shlex.split(sandbox["test_command"])
        else:
            command = workspace_sandbox.detect_test_command(worktree)
        if not command:
            yield {
                "event": "failed",
                "error": "Kein Testbefehl erkannt (pytest.ini/pyproject.toml/package.json/Cargo.toml)",
                "applied": applied,
            }
            return

        yield {"event": "activity", "text": f"laufe {command[0]} …"}
        result = workspace_sandbox.run_tests(
            worktree, command, timeout=max(10, min(int(request.timeout), 1800))
        )
        with MetadataDB(request.metadata_db_path) as db:
            db.update_code_sandbox(
                sandbox["id"],
                status="passed" if result.returncode == 0 else "failed",
                last_exit_code=result.returncode,
                test_command=" ".join(command),
            )
        yield {
            "event": "done",
            "sandbox_id": sandbox["id"],
            "run": result.as_dict(),
            "applied": applied,
            "node_id": node_id,
        }

    return _sse(events)
