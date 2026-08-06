"""Code-Graph: Positionserkennung, RPC-Rundlauf und die HTTP-Schicht.

Die Tests, die das ``cs``-Binary brauchen, überspringen sich selbst, wenn es
nicht gebaut ist — der Code-Graph ist ein Zusatz, und eine nicht gebaute
Rust-Komponente darf die Python-Suite nicht rot machen.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codegraph import binary
from codegraph.positions import Position, find_positions, find_positions_in_text

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

needs_binary = pytest.mark.skipif(
    not binary.binary_available(),
    reason="cs-Binary nicht gebaut (python packaging/build_codesearch.py)",
)

FIXTURE = (
    "def apply_discount(total, pct):\n"
    "    return total * (1 - pct)\n"
    "\n"
    "\n"
    "def checkout(cart):\n"
    "    return apply_discount(cart.total, 0.1)\n"
)


# --- Positionserkennung (kein Binary nötig) ----------------------------------


def test_python_tracebacks_are_recognised():
    assert Position("src/app.py", 42) in find_positions(
        '  File "src/app.py", line 42, in handler'
    )


def test_compiler_and_linter_positions_are_recognised():
    rust = find_positions("error[E0308]: --> crates/cs-core/src/lib.rs:120:9")
    assert Position("crates/cs-core/src/lib.rs", 120) in rust

    eslint = find_positions("web/src/App.tsx:31:5  error  Unexpected any")
    assert Position("web/src/App.tsx", 31) in eslint

    pytest_line = find_positions("tests/test_pdf_guard.py:88: AssertionError")
    assert Position("tests/test_pdf_guard.py", 88) in pytest_line


def test_ordinary_output_produces_no_false_positions():
    """Die Ablehnungen sind der eigentliche Inhalt der Erkennung."""
    assert find_positions("Alle 107 Tests bestanden") == []
    assert find_positions("fertig um 14:30") == []
    assert find_positions("Verhältnis 3:1") == []
    assert find_positions("Fortschritt 50:50") == []


def test_positions_are_deduplicated_and_capped():
    text = "\n".join(f"src/datei{n}.py:{n}: Fehler" for n in range(1, 20))
    found = find_positions_in_text(text, limit=8)
    assert len(found) == 8
    assert len(set(found)) == 8

    doubled = "a/b.py:3 und nochmal a/b.py:3"
    assert len(find_positions(doubled)) == 1


# --- RPC-Rundlauf ------------------------------------------------------------


@pytest.fixture
def indexed_workspace(tmp_path: Path):
    """Ein winziges Projekt plus Index — der Index liegt außerhalb des Projekts."""
    from codegraph.rpc import CodeGraphClient

    root = tmp_path / "projekt"
    (root / "src").mkdir(parents=True)
    (root / "src" / "pricing.py").write_text(FIXTURE, encoding="utf-8")

    db_path = tmp_path / "index" / "index.csdb"
    with CodeGraphClient(root, db_path) as client:
        client.index()
        yield client, root, db_path


@needs_binary
def test_indexing_leaves_nothing_in_the_project(indexed_workspace):
    _client, root, db_path = indexed_workspace
    assert db_path.is_file()
    assert not (
        root / ".codesearch"
    ).exists(), "ein fremdes Repository darf keinen Index-Ordner abbekommen"


@needs_binary
def test_node_ids_stay_strings_across_the_boundary(indexed_workspace):
    """Eine 64-Bit-ID als JSON-Zahl verlöre in JavaScript ihre unteren Bits."""
    client, _root, _db = indexed_workspace
    hits = client.call("search_symbols", {"query": "apply_discount"})
    assert hits, "das Symbol steht in der Datei"
    assert isinstance(hits[0]["id"], str)

    detail = client.call("node", {"id": hits[0]["id"]})
    assert detail["name"] == "apply_discount"


@needs_binary
def test_every_relationship_carries_confidence_and_evidence(indexed_workspace):
    client, _root, _db = indexed_workspace
    hits = client.call("search_symbols", {"query": "apply_discount"})
    blueprint = client.call("blueprint", {"id": hits[0]["id"]})

    assert blueprint["callers"], "checkout ruft apply_discount auf"
    for caller in blueprint["callers"]:
        assert caller["confidence"] in {"verified", "resolved", "guessed", "measured"}
        assert caller["evidence_path"]
        assert caller["evidence_line"] >= 1


@needs_binary
def test_quoting_requires_retrieval_in_the_same_session(indexed_workspace):
    """Die Belegprüfung ist der Kern des Werkzeugs und muss die Prozessgrenze überleben."""
    client, _root, _db = indexed_workspace
    claim = "Der Rabatt wird in src/pricing.py:1-2 gerechnet."

    cold = client.call("verify_citations", {"session": "kalt", "text": claim})
    assert cold["citations"][0]["status"] == "not_retrieved"

    client.call(
        "tool_call",
        {
            "session": "warm",
            "name": "read_lines",
            "arguments": {"path": "src/pricing.py", "from_line": 1, "to_line": 2},
        },
    )
    warm = client.call("verify_citations", {"session": "warm", "text": claim})
    assert warm["citations"][0]["status"] == "verified"
    assert warm["verdict"] == "sound"


@needs_binary
def test_an_invented_file_is_caught(indexed_workspace):
    client, _root, _db = indexed_workspace
    result = client.call(
        "verify_citations", {"session": "s", "text": "Siehe src/gibt/es/nicht.py:42."}
    )
    assert result["citations"][0]["status"] == "unknown_file"
    assert result["verdict"] == "broken"


@needs_binary
def test_the_eight_tools_are_offered(indexed_workspace):
    client, _root, _db = indexed_workspace
    names = {spec["function"]["name"] for spec in client.call("tool_specs")}
    assert names == {
        "search_symbols",
        "search_text",
        "get_node",
        "callers_of",
        "callees_of",
        "path_between",
        "read_lines",
        "most_relevant",
    }


@needs_binary
def test_a_stale_file_is_reported_as_stale(indexed_workspace):
    """Nach einer Änderung passen die Zeilennummern nicht mehr — das wird gesagt."""
    client, root, _db = indexed_workspace
    (root / "src" / "pricing.py").write_text("# alles anders\n", encoding="utf-8")

    source = client.call("source", {"path": "src/pricing.py"})
    assert source["stale"] is True


# --- HTTP-Schicht ------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path, monkeypatch):
    """Product-API mit eigener DuckDB und eigenem Index-Verzeichnis."""
    monkeypatch.setenv("SCIENCEKG_DISABLE_INSTANCE_LOCK", "1")
    import api.product_main as pm
    from codegraph import pool
    from storage.metadata_db import MetadataDB

    db_path = str(tmp_path / "metadata.duckdb")
    monkeypatch.setattr(binary, "index_dir", lambda *_a, **_k: tmp_path / "codegraph")
    fresh = pool.ClientPool(config_path="config.yaml")
    monkeypatch.setattr(pool, "POOL", fresh)
    from codegraph import service

    monkeypatch.setattr(service, "POOL", fresh)

    root = tmp_path / "projekt"
    (root / "src").mkdir(parents=True)
    (root / "src" / "pricing.py").write_text(FIXTURE, encoding="utf-8")

    with MetadataDB(db_path) as db:
        project = db.add_code_project(
            name="Testprojekt", path=str(root), kind="external"
        )

    with TestClient(pm.app) as client:
        yield client, str(project["id"]), db_path
    fresh.close_all()


def test_status_works_without_an_index(api):
    client, project_id, db_path = api
    response = client.get(
        f"/codegraph/{project_id}", params={"metadata_db_path": db_path}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "none"
    assert body["index_exists"] is False
    assert body["stats"]["nodes"] == 0


def test_an_unknown_project_is_a_clean_404(api):
    client, _project_id, db_path = api
    response = client.get(
        "/codegraph/cp_gibtsnicht", params={"metadata_db_path": db_path}
    )
    assert response.status_code == 404
    assert "nicht gefunden" in response.json()["detail"]


def test_a_too_short_text_query_returns_nothing_rather_than_an_error(api):
    """Die Trigramm-Suche kann unter drei Zeichen nichts — das ist kein Fehler."""
    client, project_id, db_path = api
    response = client.get(
        f"/codegraph/{project_id}/search/text",
        params={"q": "ab", "metadata_db_path": db_path},
    )
    assert response.status_code == 200
    assert response.json() == []


@needs_binary
def test_indexing_over_http_streams_progress_and_then_answers(api):
    client, project_id, db_path = api

    with client.stream(
        "POST", f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path}
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    kinds = [event["event"] for event in events]
    assert "started" in kinds
    assert (
        "progress" in kinds
    ), "ohne Zwischenmeldung wäre ein langer Lauf von einem Hänger nicht zu unterscheiden"
    assert kinds[-1] == "done"
    assert events[-1]["stats"]["nodes"] >= 2

    status = client.get(
        f"/codegraph/{project_id}", params={"metadata_db_path": db_path}
    ).json()
    assert status["status"] == "ready"
    assert status["stats"]["nodes"] >= 2

    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    assert hits and isinstance(hits[0]["id"], str)

    blueprint = client.get(
        f"/codegraph/{project_id}/blueprint/{hits[0]['id']}",
        params={"metadata_db_path": db_path},
    ).json()
    assert blueprint["callers"][0]["node"]["name"] == "checkout"

    overview = client.get(
        f"/codegraph/{project_id}/overview", params={"metadata_db_path": db_path}
    ).json()
    assert overview["stats"]["nodes"] >= 2

    dropped = client.request(
        "DELETE", f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path}
    ).json()
    assert dropped["removed"] is True


@needs_binary
def test_a_diagram_never_arrives_without_its_confidence(api):
    """Ein Bild sieht autoritativer aus als jede Textzeile.

    Fiele die Sicherheitsstufe hier weg, würde aus einer über Namensgleichheit
    geratenen Kante eine gezeichnete Tatsache.
    """
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "checkout", "metadata_db_path": db_path},
    ).json()
    node_id = hits[0]["id"]

    sequence = client.get(
        f"/codegraph/{project_id}/diagram/{node_id}",
        params={"kind": "sequence", "metadata_db_path": db_path},
    ).json()
    assert sequence["sequence"], "checkout ruft apply_discount auf"
    for step in sequence["sequence"]:
        assert step["confidence"] in {"verified", "resolved", "guessed", "measured"}
        assert step["evidence_path"]
        assert isinstance(step["to_id"], str)

    klass = client.get(
        f"/codegraph/{project_id}/diagram/{node_id}",
        params={"kind": "class", "metadata_db_path": db_path},
    )
    assert klass.status_code == 200

    unknown = client.get(
        f"/codegraph/{project_id}/diagram/{node_id}",
        params={"kind": "erfunden", "metadata_db_path": db_path},
    )
    assert unknown.status_code == 400


def test_paper_and_code_can_be_linked(api):
    client, project_id, db_path = api

    created = client.post(
        f"/codegraph/{project_id}/links",
        json={
            "paper_id": "arxiv:1706.03762",
            "project_id": "Testprojekt",
            "rel_path": "src/pricing.py",
            "start_line": 1,
            "kind": "implements",
            "metadata_db_path": db_path,
        },
    )
    assert created.status_code == 200
    link = created.json()
    assert link["paper_id"] == "arxiv:1706.03762"

    listed = client.get(
        f"/codegraph/{project_id}/links", params={"metadata_db_path": db_path}
    ).json()
    assert [item["id"] for item in listed["links"]] == [link["id"]]

    deleted = client.request(
        "DELETE",
        f"/codegraph/{project_id}/links/{link['id']}",
        params={"metadata_db_path": db_path},
    )
    assert deleted.status_code == 200


def test_a_code_citation_lands_next_to_the_paper_citations(api, tmp_path):
    """Keine zweite Tabelle — und die synthetische ``paper_id`` hält ``NOT NULL``.

    Alle bestehenden Leser von ``note_citations`` müssen unverändert weiterlaufen;
    genau dafür trägt ein Code-Zitat ``code:<projekt>:<pfad>:<zeile>`` als ID.
    """
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = api

    with MetadataDB(db_path) as db:
        note = db.create_note(
            "Testprojekt", title="Werkstattnotiz", markdown="# Notiz\n"
        )

    created = client.post(
        f"/codegraph/{project_id}/cite",
        json={
            "note_id": note["id"],
            "rel_path": "src/pricing.py",
            "start_line": 1,
            "end_line": 2,
            "metadata_db_path": db_path,
        },
    )
    assert created.status_code == 200
    citation = created.json()
    assert citation["source_kind"] == "code"
    assert citation["paper_id"] == f"code:{project_id}:src/pricing.py:1-2"
    # Der Ausschnitt kommt aus der Datei, nicht vom Aufrufer.
    assert "apply_discount" in citation["reference_text"]
    assert citation["content_hash"]

    listed = client.get(
        f"/codegraph/{project_id}/citations",
        params={"note_id": note["id"], "metadata_db_path": db_path},
    ).json()
    assert [item["id"] for item in listed["citations"]] == [citation["id"]]
    assert listed["citations"][0]["stale"] is False

    # Jetzt ändert sich die Datei: die Zeilennummer zeigt nicht mehr dorthin.
    project_root = Path(
        client.get(
            f"/codegraph/{project_id}", params={"metadata_db_path": db_path}
        ).json()["path"]
    )
    (project_root / "src" / "pricing.py").write_text(
        "# alles anders\n", encoding="utf-8"
    )

    after = client.get(
        f"/codegraph/{project_id}/citations",
        params={"note_id": note["id"], "metadata_db_path": db_path},
    ).json()
    assert after["citations"][0]["stale"] is True


def test_citing_the_same_lines_twice_updates_instead_of_duplicating(api):
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = api
    with MetadataDB(db_path) as db:
        note = db.create_note("Testprojekt", title="Notiz", markdown="")

    payload = {
        "note_id": note["id"],
        "rel_path": "src/pricing.py",
        "start_line": 1,
        "end_line": 2,
        "metadata_db_path": db_path,
    }
    first = client.post(f"/codegraph/{project_id}/cite", json=payload).json()
    second = client.post(f"/codegraph/{project_id}/cite", json=payload).json()
    assert first["id"] == second["id"]

    # Ein anderer Zeilenbereich ist dagegen ein anderes Zitat, keine Korrektur.
    other = client.post(
        f"/codegraph/{project_id}/cite",
        json={**payload, "start_line": 5, "end_line": 6},
    ).json()
    assert other["id"] != first["id"]

    with MetadataDB(db_path) as db:
        assert len(db.list_note_citations(note["id"])) == 2


def test_citing_into_a_missing_note_is_a_clean_404(api):
    client, project_id, db_path = api
    response = client.post(
        f"/codegraph/{project_id}/cite",
        json={
            "note_id": "note_gibtsnicht",
            "rel_path": "src/pricing.py",
            "start_line": 1,
            "metadata_db_path": db_path,
        },
    )
    assert response.status_code == 404


def test_an_empty_link_is_rejected(api):
    client, project_id, db_path = api
    response = client.post(
        f"/codegraph/{project_id}/links", json={"metadata_db_path": db_path}
    )
    assert response.status_code == 400


@needs_binary
def test_the_code_graph_can_be_analysis_context(api):
    """Das Repository selbst als Gegenstand einer Analyse.

    Nur der Überblick, nicht der ganze Graph: der Planer schreibt ein Skript,
    er soll nicht den Code lesen.
    """
    from api.routers.analysis import _analysis_context
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    with MetadataDB(db_path) as db:
        context = _analysis_context(db, [], [], None, project_id)
        assert context is not None
        assert "Kennzahlen:" in context
        assert "apply_discount" in context

        # Ohne Code-Projekt bleibt alles wie vorher.
        assert _analysis_context(db, [], [], None, None) is None
        # Und ein unbekanntes Projekt ist kein Fehler, sondern eben kein Kontext.
        assert _analysis_context(db, [], [], None, "cp_gibtsnicht") is None


# --- Karte, Nachbarn, Pfad, Blättern -----------------------------------------
# Die Routen, die es vorher nicht gab: `graph_slice` hatte keinen Client,
# `neighbours`/`path_between`/`top_symbols` gar keine Route. Elf Kantenarten und
# zwölf Symbolarten wurden indiziert und an der API-Grenze weggeworfen.


def test_direction_must_be_one_of_three(api):
    """Wird vor dem RPC geprüft — läuft deshalb auch ohne Binary."""
    client, project_id, db_path = api
    response = client.get(
        f"/codegraph/{project_id}/slice/deadbeefdeadbeef",
        params={"direction": "schräg", "metadata_db_path": db_path},
    )
    assert response.status_code == 400
    assert "direction" in response.json()["detail"]


def test_an_unknown_edge_kind_is_rejected_rather_than_silently_ignored(api):
    """Der eigentliche Test.

    ``EdgeKind::from_str`` verwirft auf der Rust-Seite still, was es nicht kennt,
    und der leere Rest fällt auf einen Standardsatz zurück. Ohne diese Prüfung
    sähe ``?edges=call`` aus wie ein Ergebnis.
    """
    client, project_id, db_path = api
    response = client.get(
        f"/codegraph/{project_id}/slice/deadbeefdeadbeef",
        params={"edges": "call", "metadata_db_path": db_path},
    )
    assert response.status_code == 400
    assert "call" in response.json()["detail"]

    ok_but_unknown_node = client.get(
        f"/codegraph/{project_id}/top",
        params={"kind": "erfunden", "metadata_db_path": db_path},
    )
    assert ok_but_unknown_node.status_code == 400


@needs_binary
def test_a_slice_carries_every_edge_with_its_confidence(api):
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()

    slice_ = client.get(
        f"/codegraph/{project_id}/slice/{hits[0]['id']}",
        params={"direction": "in", "depth": 2, "metadata_db_path": db_path},
    ).json()

    assert slice_["nodes"], "checkout hängt an apply_discount"
    assert slice_["edges"]
    for edge in slice_["edges"]:
        assert edge["confidence"] in {"verified", "resolved", "guessed", "measured"}
        assert edge["evidence_path"]
        assert edge["evidence_line"] >= 1


@needs_binary
def test_slice_ids_stay_strings_across_the_new_boundary(api):
    """Die neue Grenze, an der die 64-Bit-ID still zerbrechen könnte."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()

    slice_ = client.get(
        f"/codegraph/{project_id}/slice/{hits[0]['id']}",
        params={"direction": "in", "metadata_db_path": db_path},
    ).json()
    for edge in slice_["edges"]:
        assert isinstance(edge["from"], str)
        assert isinstance(edge["to"], str)
    for node in slice_["nodes"]:
        assert isinstance(node["id"], str)


@needs_binary
def test_a_slice_can_be_narrowed_to_one_edge_kind(api):
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()

    only_calls = client.get(
        f"/codegraph/{project_id}/slice/{hits[0]['id']}",
        params={"direction": "in", "edges": "calls", "metadata_db_path": db_path},
    ).json()
    assert only_calls["edges"]
    assert {edge["kind"] for edge in only_calls["edges"]} == {"calls"}


@needs_binary
def test_impact_walks_backwards_to_the_caller(api):
    """checkout ruft apply_discount — impact auf apply_discount muss checkout erreichen."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    node_id = hits[0]["id"]

    impact = client.get(
        f"/codegraph/{project_id}/impact/{node_id}",
        params={"depth": 3, "metadata_db_path": db_path},
    ).json()

    reached_names = {entry["node"]["name"] for entry in impact["reached"]}
    assert "checkout" in reached_names, f"checkout fehlt im Radius: {reached_names}"
    # Hop-Distanz und Sicherheitsstufe sind die Aussage, nicht nur die Namensliste.
    checkout_entry = next(
        e for e in impact["reached"] if e["node"]["name"] == "checkout"
    )
    assert checkout_entry["hops"] == 1
    assert checkout_entry["confidence"] in {
        "verified",
        "resolved",
        "guessed",
        "measured",
    }
    # IDs bleiben Hex-Strings — die 64-Bit-Falle greift an jeder neuen Grenze.
    assert isinstance(checkout_entry["node"]["id"], str)
    assert isinstance(impact["root"], str)
    # Ein Kappten muss sichtbar sein, nicht still.
    assert isinstance(impact["truncated"], bool)


@needs_binary
def test_hotspots_carry_the_rule_that_tripped_them(api):
    """Eine Fundstelle ohne ihre Regel wäre eine Behauptung — hier muss die Regel stehen."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    # Die Fixture-Funktionen sind klein; mit Schwellen auf 0 deaktiviert, muss
    # die Liste leer sein — die Messung, nicht eine gefühlte Gesamtnote.
    empty = client.get(
        f"/codegraph/{project_id}/hotspots",
        params={
            "loc": 0,
            "complexity": 0,
            "max_nesting": 0,
            "fan_in": 0,
            "fan_out": 0,
            "churn": 0,
            "metadata_db_path": db_path,
        },
    ).json()
    assert empty == []

    # Mit winzigen Schwellen erscheinen die Funktionen — und jede mit ihrer Regel.
    hotspots = client.get(
        f"/codegraph/{project_id}/hotspots",
        params={
            "loc": 1,
            "complexity": 1,
            "metadata_db_path": db_path,
        },
    ).json()
    assert hotspots, "mit niedrigen Schwellen müssen die Funktionen erscheinen"
    first = hotspots[0]
    assert first["rules"], "jede Fundstelle trägt ihre Regel und ihren Messwert"
    assert all(
        "rule" in r and "value" in r and "threshold" in r for r in first["rules"]
    )
    assert isinstance(first["node"]["id"], str)  # Hex-String, nicht Zahl


@needs_binary
def test_cycles_endpoint_is_a_clean_object(api):
    """``/cycles`` antwortet ohne Absturz; die Struktur steht auch bei leerem Graph."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    body = client.get(
        f"/codegraph/{project_id}/cycles",
        params={"level": "file", "metadata_db_path": db_path},
    ).json()
    assert body["level"] == "file"
    assert isinstance(body["cycles"], list)
    # Die Fixture hat keinen Ring; das ist eine gültige Antwort, kein Fehler.
    for cycle in body["cycles"]:
        assert cycle["weakest"] in {"verified", "resolved", "guessed", "measured"}
        assert cycle["edges"], "keine Kante ohne Beleg — ein Ring ohne Kanten ist keins"

    # Eine ungültige Ebene ist ein 400, kein 500.
    bad = client.get(
        f"/codegraph/{project_id}/cycles",
        params={"level": "galaxy", "metadata_db_path": db_path},
    )
    assert bad.status_code == 400


@needs_binary
def test_neighbours_reach_the_kinds_blueprint_throws_away(api):
    """``blueprint`` kennt nur calls/reads/writes — der Container fehlt dort."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    node_id = hits[0]["id"]

    container = client.get(
        f"/codegraph/{project_id}/neighbours/{node_id}",
        params={"direction": "in", "edges": "contains", "metadata_db_path": db_path},
    ).json()
    assert container, "die Datei enthält die Funktion"
    assert {item["kind"] for item in container} == {"contains"}
    for item in container:
        assert item["confidence"] in {"verified", "resolved", "guessed", "measured"}
        assert item["evidence_path"]

    blueprint = client.get(
        f"/codegraph/{project_id}/blueprint/{node_id}",
        params={"metadata_db_path": db_path},
    ).json()
    assert "contains" not in {caller["kind"] for caller in blueprint["callers"]}


@needs_binary
def test_a_path_between_two_symbols_is_a_list_of_steps(api):
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    def find(name: str) -> str:
        hits = client.get(
            f"/codegraph/{project_id}/search",
            params={"q": name, "metadata_db_path": db_path},
        ).json()
        return next(hit["id"] for hit in hits if hit["name"] == name)

    checkout, discount = find("checkout"), find("apply_discount")

    forward = client.get(
        f"/codegraph/{project_id}/path",
        params={"from": checkout, "to": discount, "metadata_db_path": db_path},
    ).json()
    assert forward["path"], "checkout ruft apply_discount auf"
    for step in forward["path"]:
        assert isinstance(step["id"], str)
        assert step["line"] >= 1

    # Rückwärts gibt es keinen Aufrufpfad. Das heisst nicht „keine Beziehung" —
    # nur, dass diese Suche calls/reads/writes verfolgt und sonst nichts.
    backward = client.get(
        f"/codegraph/{project_id}/path",
        params={"from": discount, "to": checkout, "metadata_db_path": db_path},
    ).json()
    assert backward["path"] is None


@needs_binary
def test_top_symbols_can_browse_kinds_the_overview_hardcodes(api):
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    functions = client.get(
        f"/codegraph/{project_id}/top",
        params={"kind": "function", "metadata_db_path": db_path},
    ).json()
    assert {hit["kind"] for hit in functions} == {"function"}

    # Arten, die dieses Projekt nicht hat, sind leer — aber kein Fehler.
    for kind in ("route", "db_table", "config_key", "dynamic_gap"):
        response = client.get(
            f"/codegraph/{project_id}/top",
            params={"kind": kind, "metadata_db_path": db_path},
        )
        assert response.status_code == 200


@needs_binary
def test_the_python_kind_lists_match_the_binary(api):
    """Sichert die zwei Listen, die mit ``cs-core/src/lib.rs`` synchron bleiben müssen."""
    from api.routers.codegraph import ALL_EDGE_KINDS, ALL_NODE_KINDS

    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()

    for kind in ALL_EDGE_KINDS:
        response = client.get(
            f"/codegraph/{project_id}/slice/{hits[0]['id']}",
            params={"edges": kind, "metadata_db_path": db_path},
        )
        assert response.status_code == 200, f"Kantenart {kind} kennt das Binary nicht"

    for kind in ALL_NODE_KINDS:
        response = client.get(
            f"/codegraph/{project_id}/top",
            params={"kind": kind, "limit": 1, "metadata_db_path": db_path},
        )
        assert response.status_code == 200, f"Symbolart {kind} kennt das Binary nicht"


# --- Die Bereiche (Cluster-Landkarte) ----------------------------------------


AREA_STORAGE_INIT = (
    "from storage.backends.disk import save_row\n"
    "\n"
    "\n"
    "def store(row):\n"
    "    return save_row(row)\n"
)
AREA_STORAGE_DISK = "def save_row(row):\n    return len(row)\n"
AREA_API_ROUTES = (
    "from storage import store\n"
    "\n"
    "\n"
    "def post_row(row):\n"
    "    return store(row)\n"
    "\n"
    "\n"
    "def get_row(key):\n"
    "    return store(key)\n"
)


@pytest.fixture
def cluster_api(tmp_path: Path, monkeypatch):
    """Wie ``api``, aber mit zwei Bereichen — sonst gibt es nichts zu gruppieren."""
    monkeypatch.setenv("SCIENCEKG_DISABLE_INSTANCE_LOCK", "1")
    import api.product_main as pm
    from codegraph import pool, service
    from storage.metadata_db import MetadataDB

    db_path = str(tmp_path / "metadata.duckdb")
    monkeypatch.setattr(binary, "index_dir", lambda *_a, **_k: tmp_path / "codegraph")
    fresh = pool.ClientPool(config_path="config.yaml")
    monkeypatch.setattr(pool, "POOL", fresh)
    monkeypatch.setattr(service, "POOL", fresh)

    root = tmp_path / "projekt"
    (root / "storage" / "backends").mkdir(parents=True)
    (root / "api").mkdir(parents=True)
    (root / "storage" / "__init__.py").write_text(AREA_STORAGE_INIT, encoding="utf-8")
    (root / "storage" / "backends" / "disk.py").write_text(
        AREA_STORAGE_DISK, encoding="utf-8"
    )
    (root / "api" / "routes.py").write_text(AREA_API_ROUTES, encoding="utf-8")

    with MetadataDB(db_path) as db:
        project = db.add_code_project(name="Bereiche", path=str(root), kind="external")

    with TestClient(pm.app) as client:
        project_id = str(project["id"])
        client.post(
            f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path}
        )
        yield client, project_id, db_path
    fresh.close_all()


def test_clusters_reject_an_unknown_edge_kind(api):
    """Wie überall sonst: ein Tippfehler ist eine 400, kein leeres Ergebnis."""
    client, project_id, db_path = api
    response = client.get(
        f"/codegraph/{project_id}/clusters",
        params={"edges": "quatsch", "metadata_db_path": db_path},
    )
    assert response.status_code == 400


@needs_binary
def test_the_top_level_is_the_directory_tree_and_descends(cluster_api):
    client, project_id, db_path = cluster_api

    top = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()
    assert top["prefix"] == ""
    assert top["parent"] is None
    paths = {node["path"] for node in top["nodes"]}
    assert {"storage", "api"} <= paths, paths

    # Bereichs-IDs sind Pfade, keine Zahlen — sie werden in Deep-Links geführt.
    for node in top["nodes"]:
        assert isinstance(node["path"], str)
        assert node["symbols"] >= 1

    storage = next(node for node in top["nodes"] if node["path"] == "storage")
    assert storage["has_children"] is True

    deeper = client.get(
        f"/codegraph/{project_id}/clusters",
        params={"prefix": "storage", "metadata_db_path": db_path},
    ).json()
    assert deeper["parent"] == ""
    deeper_paths = {node["path"] for node in deeper["nodes"]}
    assert "storage/backends" in deeper_paths, deeper_paths
    assert any(
        path.endswith(".py") for path in deeper_paths
    ), "eine Datei direkt im Präfix ist ein eigener Bereich, kein namenloser Rest"


@needs_binary
def test_an_aggregated_edge_opens_into_real_edges_with_evidence(cluster_api):
    """Die Zahl auf dem Pfeil muss nachschlagbar sein, sonst ist sie eine Behauptung."""
    client, project_id, db_path = cluster_api

    top = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()
    assert top["edges"], "api hängt von storage ab"

    rank = {"guessed": 0, "resolved": 1, "verified": 2, "measured": 3}
    for edge in top["edges"]:
        assert edge["from"] != edge["to"], "ein Bereich ist nicht sein eigener Nachbar"
        assert edge["count"] >= 1

        detail = client.get(
            f"/codegraph/{project_id}/clusters/edge",
            params={
                "from": edge["from"],
                "to": edge["to"],
                "metadata_db_path": db_path,
            },
        ).json()
        assert detail, "eine aufsummierte Kante muss sich öffnen lassen"

        weakest = min(rank[one["confidence"]] for one in detail)
        assert (
            rank[edge["weakest"]] == weakest
        ), "aufsummiert wird die schwächste Stufe, nicht die häufigste"
        for one in detail:
            assert one["evidence_path"]
            assert one["evidence_line"] >= 1
            assert isinstance(one["node"]["id"], str)


@needs_binary
def test_without_an_llm_the_map_still_carries_directory_names(cluster_api):
    """Der Name ist Komfort, die Struktur ist die Aussage."""
    client, project_id, db_path = cluster_api

    top = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()
    for node in top["nodes"]:
        assert node["label"], "ohne hinterlegten Namen steht der Ordnername da"
        assert node["label_source"] == "directory"
        assert node["label_stale"] is False
        assert node["purpose"] is None


@needs_binary
def test_a_stored_name_goes_stale_when_the_cluster_changes(cluster_api, tmp_path):
    """Ein Name, der nicht mehr passt, wird gekennzeichnet — nicht still behalten."""
    from codegraph import clusters as cluster_service
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = cluster_api
    top = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()
    storage = next(node for node in top["nodes"] if node["path"] == "storage")

    with MetadataDB(db_path) as db:
        db.upsert_cluster_label(
            project_id,
            "storage",
            fingerprint=cluster_service.fingerprint(storage),
            label="Datenhaltung",
            purpose="Legt Zeilen ab.",
        )

    labelled = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()
    storage = next(node for node in labelled["nodes"] if node["path"] == "storage")
    assert storage["label"] == "Datenhaltung"
    assert storage["label_source"] == "llm"
    assert storage["label_stale"] is False

    # Der Bereich bekommt ein weiteres Symbol und ist damit ein anderer.
    project_root = Path(
        client.get(
            f"/codegraph/{project_id}", params={"metadata_db_path": db_path}
        ).json()["path"]
    )
    (project_root / "storage" / "cache.py").write_text(
        "def drop_all():\n    return 0\n", encoding="utf-8"
    )
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    after = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()
    storage = next(node for node in after["nodes"] if node["path"] == "storage")
    assert storage["label"] == "Datenhaltung", "der Name bleibt sichtbar"
    assert storage["label_stale"] is True, "aber er gilt als überholt"


@needs_binary
def test_cluster_members_lead_from_an_area_into_its_symbols(cluster_api):
    client, project_id, db_path = cluster_api
    members = client.get(
        f"/codegraph/{project_id}/clusters/members",
        params={"prefix": "api", "metadata_db_path": db_path},
    ).json()
    names = {member["name"] for member in members}
    assert "post_row" in names, names
    assert all(member["kind"] not in {"file", "module"} for member in members)


class _NamingRouter:
    """Nur so viel Router, wie die Benennung anfasst."""

    default_provider = "fake"

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[list[dict]] = []

    def provider_default_model(self, _provider):
        return "fake-model"

    def chat_json(self, messages, provider=None, overrides=None):
        self.calls.append(messages)
        return self.payload


@needs_binary
def test_naming_a_level_writes_labels_without_touching_the_structure(cluster_api):
    from codegraph import clusters as cluster_service
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = cluster_api
    before = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()

    router = _NamingRouter(
        {
            "bereiche": [
                {"pfad": "storage", "name": "Datenhaltung", "zweck": "Legt Zeilen ab."},
                {
                    "pfad": "api",
                    "name": "Aussenkante",
                    "zweck": "Nimmt Anfragen entgegen.",
                },
                # Ein Bereich, den es nicht gibt: darf nichts anlegen.
                {"pfad": "erfunden", "name": "Nebel", "zweck": "Gibt es nicht."},
            ]
        }
    )

    with MetadataDB(db_path) as db:
        project = db.get_code_project(project_id)
        events = list(
            cluster_service.name_level_stream(
                project, prefix="", code_project_id=project_id, db=db, router=router
            )
        )

    assert events[-1]["event"] == "done"
    written = {record["cluster_path"] for record in events[-1]["labels"]}
    assert written == {"storage", "api"}, "erfundene Pfade werden verworfen"

    # Das Modell sieht Kennzahlen, keinen Quelltext.
    prompt = router.calls[0][-1]["content"]
    assert "save_row" in prompt, "die wichtigsten Symbolnamen gehören dazu"
    assert (
        "def save_row" not in prompt
    ), "Quelltext gehört nicht in den Benennungs-Prompt"

    after = client.get(
        f"/codegraph/{project_id}/clusters", params={"metadata_db_path": db_path}
    ).json()
    assert [node["path"] for node in after["nodes"]] == [
        node["path"] for node in before["nodes"]
    ], "die Struktur ist vom Modell nicht beeinflussbar"
    storage = next(node for node in after["nodes"] if node["path"] == "storage")
    assert storage["label"] == "Datenhaltung"
    assert storage["purpose"] == "Legt Zeilen ab."
    assert storage["label_source"] == "llm"


@needs_binary
def test_a_model_that_returns_nothing_usable_fails_loudly(cluster_api):
    from codegraph import clusters as cluster_service
    from storage.metadata_db import MetadataDB

    _client, project_id, db_path = cluster_api
    router = _NamingRouter({"bereiche": []})

    with MetadataDB(db_path) as db:
        project = db.get_code_project(project_id)
        events = list(
            cluster_service.name_level_stream(
                project, prefix="", code_project_id=project_id, db=db, router=router
            )
        )

    assert (
        events[-1]["event"] == "failed"
    ), "eine leere Benennung ist ein Fehler, keine stillschweigend leere Ebene"


# --- Eine einzelne Funktion ändern -------------------------------------------


def test_splicing_replaces_exactly_those_lines_and_nothing_else():
    """Der Rest der Datei muss byte-gleich bleiben — auch die Leerzeilen."""
    from api.routers.codegraph import _splice_lines

    updated = _splice_lines(FIXTURE, 5, 6, "def checkout(cart):\n    return cart.total")

    assert updated.startswith(
        "def apply_discount(total, pct):\n    return total * (1 - pct)\n\n\n"
    )
    assert updated.endswith("def checkout(cart):\n    return cart.total\n")
    assert (
        updated.count("\n\n\n") == 1
    ), "die beiden Leerzeilen davor bleiben unangetastet"


def test_splicing_keeps_the_line_endings_the_file_already_had():
    """Ein Editor liefert ``\\n``; eine CRLF-Datei bekäme sonst gemischte Enden.

    Gemischt heisst: jede Zeile der Datei sähe im git-Diff geändert aus, und die
    eine geänderte Funktion wäre darin nicht mehr zu finden.
    """
    from api.routers.codegraph import _splice_lines

    original = "eins\r\nzwei\r\ndrei\r\n"
    updated = _splice_lines(original, 2, 2, "ZWEI\nZWEIB")

    assert updated == "eins\r\nZWEI\r\nZWEIB\r\ndrei\r\n"
    assert updated.count("\n") == updated.count("\r\n"), "kein einziges nacktes \\n"


def test_splicing_the_last_line_adds_no_newline_at_the_end():
    """Ohne das wüchse die Datei bei jedem Speichern um eine Leerzeile."""
    from api.routers.codegraph import _splice_lines

    assert _splice_lines("a\nb", 2, 2, "B") == "a\nB"
    assert _splice_lines("a\nb\n", 2, 2, "B") == "a\nB\n"


@needs_binary
def test_a_symbol_can_be_read_and_written_without_touching_the_rest(api, tmp_path):
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "apply_discount")

    body = client.get(
        f"/codegraph/{project_id}/symbol/{node_id}/source",
        params={"metadata_db_path": db_path},
    ).json()
    assert body["path"] == "src/pricing.py"
    assert body["start_line"] == 1
    assert body["text"].startswith("def apply_discount")
    assert "def checkout" not in body["text"], "nur diese Funktion, nicht die Datei"
    assert body["stale"] is False

    written = client.patch(
        f"/codegraph/{project_id}/symbol/{node_id}/source",
        json={
            "text": "def apply_discount(total, pct):\n    # neu\n    return total * (1 - pct)",
            "content_hash": body["content_hash"],
            "metadata_db_path": db_path,
        },
    )
    assert written.status_code == 200
    assert (
        written.json()["index_stale"] is True
    ), "der Graph kennt jetzt einen alten Stand"

    on_disk = (tmp_path / "projekt" / "src" / "pricing.py").read_text(encoding="utf-8")
    assert "# neu" in on_disk
    assert on_disk.endswith(
        "def checkout(cart):\n    return apply_discount(cart.total, 0.1)\n"
    ), "was nach der Funktion stand, steht unverändert noch da"


@needs_binary
def test_writing_over_someone_elses_change_is_refused(api, tmp_path):
    """Optimistische Sperre: stilles Überschreiben wäre Datenverlust ohne Spur."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "apply_discount")
    body = client.get(
        f"/codegraph/{project_id}/symbol/{node_id}/source",
        params={"metadata_db_path": db_path},
    ).json()

    response = client.patch(
        f"/codegraph/{project_id}/symbol/{node_id}/source",
        json={
            "text": "def apply_discount(total, pct):\n    return 0",
            "content_hash": "0" * 64,
            "metadata_db_path": db_path,
        },
    )
    assert response.status_code == 409
    assert "geändert" in response.json()["detail"]
    assert (tmp_path / "projekt" / "src" / "pricing.py").read_text(
        encoding="utf-8"
    ) == FIXTURE

    # Mit dem richtigen Hash geht derselbe Schreibvorgang durch.
    assert (
        client.patch(
            f"/codegraph/{project_id}/symbol/{node_id}/source",
            json={
                "text": "def apply_discount(total, pct):\n    return 0",
                "content_hash": body["content_hash"],
                "metadata_db_path": db_path,
            },
        ).status_code
        == 200
    )


@needs_binary
def test_writing_against_a_stale_index_is_refused(api, tmp_path):
    """Veralteter Index heisst: der Zeilenbereich zeigt woandershin.

    Geschrieben würde dann an eine Stelle, die niemand gesehen hat — schlimmer
    als eine Fehlermeldung.
    """
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "apply_discount")

    target = tmp_path / "projekt" / "src" / "pricing.py"
    target.write_text("# eine ganz andere Datei\n" + FIXTURE, encoding="utf-8")

    read = client.get(
        f"/codegraph/{project_id}/symbol/{node_id}/source",
        params={"metadata_db_path": db_path},
    ).json()
    assert read["stale"] is True, "das wird gesagt, nicht verschwiegen"

    response = client.patch(
        f"/codegraph/{project_id}/symbol/{node_id}/source",
        json={
            "text": "def apply_discount(total, pct):\n    return 0",
            "content_hash": read["content_hash"],
            "metadata_db_path": db_path,
        },
    )
    assert response.status_code == 409
    assert "indizieren" in response.json()["detail"]


@needs_binary
def test_an_unknown_symbol_is_a_clean_404(api):
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    response = client.get(
        f"/codegraph/{project_id}/symbol/deadbeefdeadbeef/source",
        params={"metadata_db_path": db_path},
    )
    assert response.status_code == 404


# --- „Warum wurde das so gebaut?" --------------------------------------------


def test_git_history_of_lines_says_why_it_has_nothing_instead_of_throwing(tmp_path):
    """Kein Repo, keine Datei, nie committet — drei Normalzustände, kein Fehler.

    Ein leeres ``commits: []`` allein sähe aus wie „diese Zeilen wurden nie
    geändert", und das ist etwas völlig anderes als „hier wird gar nicht
    versioniert".
    """
    from workspace.manager import git_log_for_lines

    plain = tmp_path / "ohne-git"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert git_log_for_lines(plain, "a.py", 1, 1) == {
        "available": False,
        "reason": "no_repo",
        "commits": [],
    }

    import subprocess

    repo = tmp_path / "mit-git"
    repo.mkdir()
    if (
        subprocess.run(["git", "init", "-q", str(repo)], capture_output=True).returncode
        != 0
    ):
        pytest.skip("kein git auf diesem Rechner")
    (repo / "neu.py").write_text("y = 2\n", encoding="utf-8")

    empty = git_log_for_lines(repo, "neu.py", 1, 1)
    assert empty["available"] is False
    assert empty["reason"] == "no_commits", "ein frisches Repo ist kein Fehler"

    (repo / "andere.py").write_text("z = 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "andere.py"], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "erste"],
        cwd=repo,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )

    untracked = git_log_for_lines(repo, "neu.py", 1, 1)
    assert untracked["available"] is False
    assert (
        untracked["reason"] == "untracked"
    ), "eine nie committete Datei ist kein Fehler"


def test_git_history_of_lines_reports_the_commits_that_touched_them(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    if (
        subprocess.run(["git", "init", "-q", str(repo)], capture_output=True).returncode
        != 0
    ):
        pytest.skip("kein git auf diesem Rechner")
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }

    def commit(message: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", message],
            cwd=repo,
            capture_output=True,
            env={**os.environ, **env},
        )

    target = repo / "app.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    commit("erste Fassung")
    target.write_text(
        "def f():\n    # Sonderfall: 0 ist erlaubt\n    return 1\n", encoding="utf-8"
    )
    commit("Sonderfall 0 zulassen")

    from workspace.manager import git_log_for_lines

    history = git_log_for_lines(repo, "app.py", 2, 2)
    assert history["available"] is True
    subjects = [entry["subject"] for entry in history["commits"]]
    assert (
        "Sonderfall 0 zulassen" in subjects
    ), "der Betreff ist die eigentliche Begründung"
    assert all(entry["date"] and entry["author"] for entry in history["commits"])


@needs_binary
def test_a_stored_rationale_goes_stale_when_the_file_changes(api, tmp_path):
    """Sonst stünde eine Begründung als Aussage über Code, den sie nie gesehen hat."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "apply_discount")

    saved = client.post(
        f"/codegraph/{project_id}/rationale",
        json={
            "symbol_id": node_id,
            "text": "Prozentwert statt Faktor, weil die Preisliste ihn so liefert.",
            "metadata_db_path": db_path,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["stale"] is False
    assert (
        saved.json()["rel_path"] == "src/pricing.py"
    ), "der Pfad kommt aus dem Graphen"

    fresh = client.get(
        f"/codegraph/{project_id}/why/{node_id}", params={"metadata_db_path": db_path}
    ).json()
    assert [note["stale"] for note in fresh["notes"]] == [False]

    (tmp_path / "projekt" / "src" / "pricing.py").write_text(
        FIXTURE.replace("0.1", "0.2"), encoding="utf-8"
    )
    after = client.get(
        f"/codegraph/{project_id}/why/{node_id}", params={"metadata_db_path": db_path}
    ).json()
    assert [note["stale"] for note in after["notes"]] == [True]

    listing = client.get(
        f"/codegraph/{project_id}/rationale", params={"metadata_db_path": db_path}
    ).json()
    assert listing["rationale"][0]["stale"] is True

    rationale_id = saved.json()["id"]
    assert (
        client.delete(
            f"/codegraph/{project_id}/rationale/{rationale_id}",
            params={"metadata_db_path": db_path},
        ).status_code
        == 200
    )


@needs_binary
def test_the_recorded_half_stands_without_any_model(api):
    """Kein LLM angefasst — git und die eigenen Notizen sind Beleg, keine Antwort."""
    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "checkout", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "checkout")

    body = client.get(
        f"/codegraph/{project_id}/why/{node_id}", params={"metadata_db_path": db_path}
    ).json()
    assert body["path"] == "src/pricing.py"
    assert body["start_line"] >= 1
    # Der Testordner ist kein Repository — und genau das steht dann da.
    assert body["history"]["available"] is False
    assert body["history"]["reason"] in {"no_repo", "no_git", "no_commits", "untracked"}
    assert body["notes"] == []


class _WhyRouter:
    """Nur so viel Router, wie die Herleitung anfasst."""

    default_provider = "fake"

    def __init__(self, text):
        self.text = text
        self.calls: list[list[dict]] = []

    def provider_default_model(self, _provider):
        return "fake-model"

    def chat_with_tools(self, messages, tools, provider=None, overrides=None):
        self.calls.append(messages)
        return self.text, []


@needs_binary
def test_a_derivation_is_marked_as_one_and_never_mixed_with_the_record(api):
    """Die zwei Hälften dürfen nie in einem Block landen.

    Eine erfundene Absicht lässt sich nicht wie ein erfundenes ``datei:zeile``
    nachprüfen — deshalb trägt die hergeleitete Hälfte ``kind: "derived"`` und
    sagt daneben, worauf sie beruht.
    """
    from codegraph import rationale as rationale_module
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "apply_discount", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "apply_discount")

    router = _WhyRouter(
        "Die Funktion rechnet einen Rabatt auf einen Betrag. "
        "Sie nimmt einen Anteil statt eines Faktors, weil der Aufrufer sie so benutzt "
        "(src/pricing.py:6)."
    )
    with MetadataDB(db_path) as db:
        answer = rationale_module.why(
            project=db.get_code_project(project_id),
            node_id=node_id,
            code_project_id=project_id,
            db=db,
            router=router,
        )

    assert answer["kind"] == "derived"
    assert answer["based_on"]["callers"] >= 1, "checkout ruft apply_discount auf"
    assert "verdict" in answer, "auch eine Herleitung geht durch die Belegprüfung"

    prompt = router.calls[0][-1]["content"]
    assert "commit_betreffe" in prompt
    assert "aufrufende_tests" in prompt


@needs_binary
def test_a_derivation_without_ground_says_so_rather_than_inventing_one(api):
    from codegraph import rationale as rationale_module
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "checkout", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "checkout")

    router = _WhyRouter("Aus Code, Tests und Historie lässt sich kein Grund herleiten.")
    with MetadataDB(db_path) as db:
        answer = rationale_module.why(
            project=db.get_code_project(project_id),
            node_id=node_id,
            code_project_id=project_id,
            db=db,
            router=router,
        )

    assert "kein Grund herleiten" in answer["text"]
    # Der Prompt muss das ausdrücklich erlauben, sonst füllt ein Modell die Lücke.
    assert "kein Grund herleiten" in rationale_module.SYSTEM_PROMPT


@needs_binary
def test_an_empty_derivation_fails_loudly(api):
    from codegraph import rationale as rationale_module
    from storage.metadata_db import MetadataDB

    client, project_id, db_path = api
    client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})
    hits = client.get(
        f"/codegraph/{project_id}/search",
        params={"q": "checkout", "metadata_db_path": db_path},
    ).json()
    node_id = next(hit["id"] for hit in hits if hit["name"] == "checkout")

    with MetadataDB(db_path) as db:
        with pytest.raises(RuntimeError):
            rationale_module.why(
                project=db.get_code_project(project_id),
                node_id=node_id,
                code_project_id=project_id,
                db=db,
                router=_WhyRouter("   "),
            )
