"""Der Code-Begleiter: die Werkzeugschleife und die Beleg-Invariante.

Das Modell ist hier immer gefälscht — was geprüft wird, ist nicht, ob ein LLM
etwas Kluges sagt, sondern ob eine **erfundene Belegstelle als solche ankommt**.
Genau dafür existiert das Werkzeug: eine echte Datei mit einer plausiblen
Zeilennummer sieht in einer Antwort völlig unauffällig aus.

Die Tests, die das ``cs``-Binary brauchen, überspringen sich selbst.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codegraph import binary, companion
from codegraph.companion import TrailStep, parse_trail, split_trail
from query.llm_router import ToolCall

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _extract_rust_system_prompt() -> str:
	"""Lies die Rust-Konstante aus ``cs-llm/src/lib.rs`` als Rohtext.

	Der Systemprompt existiert zweimal wortgleich (Python + Rust). Ohne diesen
	Test driften sie still auseinander, und CLI und App verhalten sich
	unterschiedlich — der Kopfhinweis in ``companion.py`` verpflichtet dazu,
	 beide zu ändern.
	"""
	import re

	root = Path(__file__).resolve().parent.parent
	lib_rs = root / "codesearch" / "crates" / "cs-llm" / "src" / "lib.rs"
	text = lib_rs.read_text(encoding="utf-8")
	match = re.search(r'const SYSTEM_PROMPT:\s*&str\s*=\s*r#"(?P<body>.*?)"#;', text, re.DOTALL)
	assert match is not None, "SYSTEM_PROMPT in cs-llm/src/lib.rs nicht gefunden"
	return match.group("body")


def test_system_prompt_is_identical_in_rust_and_python() -> None:
	"""Der Prompt darf nicht driftent — siehe Kopfhinweis in companion.py."""
	assert companion.SYSTEM_PROMPT == _extract_rust_system_prompt()

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


class FakeRouter:
    """Ein Modell, dessen Antworten der Test vorgibt.

    ``turns`` ist eine Liste von ``(text, tool_calls)``; jede Runde nimmt die
    nächste. Die gestellten Nachrichten werden mitgeschrieben, damit ein Test
    prüfen kann, was das Modell überhaupt zu sehen bekam.
    """

    default_provider = "fake"

    def __init__(self, turns: list[tuple[str | None, list[ToolCall]]]) -> None:
        self.turns = list(turns)
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []
        self.last_response_metadata: dict[str, Any] = {}

    def provider_default_model(self, _provider: str | None = None) -> str:
        return "fake-model"

    def chat_with_tools(self, messages, tools, provider=None, overrides=None):
        self.calls.append(([dict(message) for message in messages], tools))
        if not self.turns:
            return "", []
        return self.turns.pop(0)


@pytest.fixture
def project(tmp_path: Path):
    """Ein indiziertes Miniprojekt, angesprochen wie ein Werkstatt-Projekt."""
    from codegraph import pool, service

    root = tmp_path / "projekt"
    (root / "src").mkdir(parents=True)
    (root / "src" / "pricing.py").write_text(FIXTURE, encoding="utf-8")

    fresh = pool.ClientPool(config_path="config.yaml")
    original = service.POOL
    service.POOL = fresh
    original_index_dir = binary.index_dir
    binary.index_dir = lambda *_a, **_k: tmp_path / "codegraph"  # type: ignore[assignment]

    record = {"id": "cp_test", "name": "Testprojekt", "path": str(root), "kind": "external"}
    client = service.client_for(record)
    client.index()
    try:
        yield record, root
    finally:
        service.POOL = original
        binary.index_dir = original_index_dir  # type: ignore[assignment]
        fresh.close_all()


def _answer(project_record, router, question="Wie wird der Rabatt gerechnet?", **kwargs):
    events = list(companion.ask_stream(project_record, question, router=router, **kwargs))
    assert events[-1]["event"] == "done", events[-1]
    return events[-1]["answer"], events


# --- Reine Funktionen (kein Binary nötig) ------------------------------------


def test_the_trail_block_is_separated_from_the_prose():
    prose, trail = split_trail("Der Betrag wird gerundet.\n\nSPUR:\na.py:1 — hier rein")
    assert prose == "Der Betrag wird gerundet."
    assert trail == "a.py:1 — hier rein"

    assert split_trail("Nur Prosa.") == ("Nur Prosa.", "")


def test_trail_lines_survive_both_dash_shapes_and_bullets():
    steps = parse_trail(
        "- src/a.py:31 — was hier passiert\n"
        "* src/b.py:88 - und hier\n"
        "src/c.py:10-20 — ein Bereich\n"
        "reine Prosa ohne Position\n"
    )
    assert [(step.path, step.line, step.reason) for step in steps] == [
        ("src/a.py", 31, "was hier passiert"),
        ("src/b.py", 88, "und hier"),
        ("src/c.py", 10, "ein Bereich"),
    ]


def test_a_step_without_a_usable_position_is_dropped_not_guessed():
    assert parse_trail("Kapitel 3: Einleitung — dazu\n") == []
    assert parse_trail("src/a.py:0 — Zeile null gibt es nicht\n") == []


def test_a_bare_numeric_citation_is_counted_as_the_error_it_is():
    """``[1]`` ist laut CLAUDE.md ein Qualitätsfehler und darf nicht durchrutschen."""
    assert companion._bare_numeric_citations("Wie in [1] gezeigt und in [12] auch.") == 2
    assert companion._bare_numeric_citations("Siehe [arxiv:2401.01234].") == 0


def test_citation_offsets_are_converted_for_javascript():
    """Rust zählt Bytes, JavaScript zählt UTF-16-Einheiten.

    Ohne die Umrechnung liefe der Beleg-Chip um jedes Umlaut-Byte nach links —
    und markierte im Antworttext die falsche Stelle.
    """
    text = "Grün und schön: src/a.py:12"
    byte_start = text.encode("utf-8").index(b"src/a.py")
    start, end = companion._utf16_offsets(text, byte_start, byte_start + len("src/a.py:12"))
    assert text[start:end] == "src/a.py:12"


# --- Die Schleife gegen den echten Graphen -----------------------------------


@needs_binary
def test_an_invented_line_number_is_reported_instead_of_believed(project):
    """Der zentrale Test: eine echte Datei mit einer erfundenen Zeile."""
    record, _root = project
    router = FakeRouter([("Der Rabatt wird in src/pricing.py:400 gerechnet.", [])])
    answer, _events = _answer(record, router)

    assert answer["citations"], "die Belegstelle muss erkannt werden"
    assert answer["citations"][0]["status"] != "verified"
    assert answer["verdict"] == "broken"
    assert answer["is_clean"] is False


@needs_binary
def test_an_invented_file_is_caught(project):
    record, _root = project
    router = FakeRouter([("Siehe src/gibt/es/nicht.py:42.", [])])
    answer, _events = _answer(record, router)
    assert answer["citations"][0]["status"] == "unknown_file"


@needs_binary
def test_what_the_retrieval_showed_may_be_quoted(project):
    """Die Vorab-Suche ist selbst eine Lizenz zum Zitieren — sonst wäre jede
    Antwort aus dem mitgelieferten Kontext fälschlich „unbelegt"."""
    record, _root = project
    router = FakeRouter([("apply_discount rechnet in src/pricing.py:1-2.", [])])
    answer, _events = _answer(record, router, question="Was macht apply_discount?")

    assert answer["citations"][0]["status"] == "verified"
    assert answer["verdict"] == "sound"


@needs_binary
def test_a_tool_call_is_dispatched_and_its_result_comes_back(project):
    """Die Runde-zwei-Nachrichten müssen das Werkzeugergebnis enthalten.

    Ohne das antwortet das Modell in Runde zwei auf denselben Stand wie in
    Runde eins — die Schleife dreht sich, ohne dass irgendwo ein Fehler entsteht.
    """
    record, _root = project
    call = ToolCall(
        id="call_0",
        name="read_lines",
        arguments=json.dumps({"path": "src/pricing.py", "from_line": 1, "to_line": 2}),
    )
    router = FakeRouter(
        [
            (None, [call]),
            ("Er multipliziert mit (1 - pct), siehe src/pricing.py:1-2.", []),
        ]
    )
    # Eine Frage ohne Namenstreffer: nur dann werden überhaupt Werkzeuge angeboten.
    answer, events = _answer(record, router, question="Warum ist das so gebaut?")

    assert answer["tool_calls"] == 1
    assert answer["truncated"] is False
    activity = [event["text"] for event in events if event["event"] == "activity"]
    assert "lese src/pricing.py" in activity

    second_round_messages = router.calls[1][0]
    tool_message = second_round_messages[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_0"
    assert "apply_discount" in tool_message["content"]

    assert answer["citations"][0]["status"] == "verified"


@needs_binary
def test_tools_are_withheld_when_retrieval_already_matched_by_name(project):
    """Kontext *und* Werkzeuge lassen kleine Modelle trotzdem nachschlagen —
    neun Aufrufe für eine Frage. Deshalb: entweder oder."""
    record, _root = project
    router = FakeRouter([("apply_discount, siehe src/pricing.py:1.", [])])
    _answer(record, router, question="Was macht apply_discount?")

    _messages, tools = router.calls[0]
    assert tools is None


@needs_binary
def test_a_trail_step_pointing_at_unread_code_is_marked_not_dropped(project):
    """Einen Schritt stillschweigend zu streichen, verstecke genau den Fehler,
    den er sichtbar macht."""
    record, _root = project
    router = FakeRouter(
        [
            (
                "apply_discount rechnet den Rabatt.\n\n"
                "SPUR:\n"
                "src/pricing.py:1 — hier wird gerechnet\n"
                "src/pricing.py:900 — hier angeblich auch\n",
                [],
            )
        ]
    )
    answer, _events = _answer(record, router, question="Was macht apply_discount?")

    trail = answer["trail"]
    assert [step["line"] for step in trail] == [1, 900]
    assert trail[0]["verified"] is True
    assert trail[1]["verified"] is False


@needs_binary
def test_an_empty_model_answer_fails_loudly(project):
    record, _root = project
    router = FakeRouter([("   ", [])])
    events = list(companion.ask_stream(record, "Was macht das hier?", router=router))
    assert events[-1]["event"] == "failed"
    assert "keine Antwort" in events[-1]["error"]


@needs_binary
def test_a_new_question_starts_with_an_empty_licence_to_quote(project):
    """Was in einem früheren Gespräch nachgeschlagen wurde, zählt jetzt nicht mehr.

    Ohne das würde eine lange Sitzung die Prüfung schrittweise aushöhlen: irgendwann
    wäre alles einmal abgerufen worden, und jede Zeilennummer ginge durch.
    """
    from codegraph import service

    record, root = project
    # Eine zweite Datei, die zur Frage nach ``apply_discount`` nicht mit
    # hervorgeholt wird — nur so lässt sich die alte von der neuen Lizenz trennen.
    (root / "src" / "legacy.py").write_text(
        "def unrelated_legacy_routine():\n" + "".join(f"    step_{n} = {n}\n" for n in range(1, 40)),
        encoding="utf-8",
    )
    service.query(record, "index", {})

    service.query(
        record,
        "tool_call",
        {
            "session": "s1",
            "name": "read_lines",
            "arguments": {"path": "src/legacy.py", "from_line": 30, "to_line": 34},
        },
    )
    warm = service.query(
        record, "verify_citations", {"session": "s1", "text": "Siehe src/legacy.py:30-34."}
    )
    assert warm["citations"][0]["status"] == "verified", "der Aufbau des Tests selbst"

    router = FakeRouter([("Siehe src/legacy.py:30-34.", [])])
    answer, _events = _answer(record, router, question="Was macht apply_discount?", session="s1")
    assert answer["citations"][0]["status"] == "not_retrieved"


# --- Papers ↔ Code -----------------------------------------------------------


def test_paper_evidence_fails_soft_when_there_is_no_database():
    """Papers sind eine Zugabe. Der Code-Teil der Antwort darf nicht mitfallen."""
    assert (
        companion._paper_evidence(
            "irgendwas",
            research_project_id=None,
            paper_ids=None,
            limit=3,
            metadata_db_path="/gibt/es/nicht/metadata.duckdb",
        )
        == []
    )


@needs_binary
def test_papers_change_the_rules_and_are_tracked_separately(project, monkeypatch):
    """Mit Papers gilt die ``[arxiv:…]``-Regel aus CLAUDE.md zusätzlich.

    Beide Belegarten müssen unterscheidbar bleiben — eine Paper-Aussage mit einer
    Codezeile zu belegen wäre genau die Verwechslung, die die Synthese wertlos
    macht.
    """
    record, _root = project
    monkeypatch.setattr(
        companion,
        "_paper_evidence",
        lambda *_a, **_k: [
            companion.PaperEvidence(
                paper_id="arxiv:1706.03762",
                title="Attention Is All You Need",
                year=2017,
                snippets=["Wir schlagen die Transformer-Architektur vor."],
            )
        ],
    )

    router = FakeRouter(
        [("Wie in [arxiv:1706.03762] beschrieben, siehe src/pricing.py:1-2. Ferner [1].", [])]
    )
    answer, _events = _answer(
        record, router, question="Was macht apply_discount?", use_papers=True
    )

    system_prompt = router.calls[0][0][0]["content"]
    assert "[arxiv:" in system_prompt, "die Paper-Regel muss im Systemprompt stehen"
    user_prompt = router.calls[0][0][1]["content"]
    assert "Attention Is All You Need" in user_prompt

    assert answer["paper_citations"] == ["arxiv:1706.03762"]
    # Ein nacktes [1] ist ein Qualitätsfehler und wird gemeldet, nicht geschluckt.
    assert answer["bare_citations"] == 1
    # Und die Code-Seite wird davon unberührt weiter in Rust geprüft.
    assert answer["citations"][0]["status"] == "verified"


@needs_binary
def test_an_invented_paper_id_is_not_counted_as_a_citation(project, monkeypatch):
    record, _root = project
    monkeypatch.setattr(
        companion,
        "_paper_evidence",
        lambda *_a, **_k: [companion.PaperEvidence(paper_id="arxiv:1706.03762", title="T")],
    )
    router = FakeRouter([("Steht in [arxiv:9999.99999].", [])])
    answer, _events = _answer(record, router, use_papers=True)
    assert answer["paper_citations"] == []


@needs_binary
def test_without_the_switch_no_paper_rule_appears(project):
    record, _root = project
    router = FakeRouter([("Kurze Antwort.", [])])
    _answer(record, router)
    assert "[arxiv:" not in router.calls[0][0][0]["content"]


# --- Über HTTP ---------------------------------------------------------------


@needs_binary
def test_asking_over_http_streams_activity_and_stores_the_answer(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

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
    (root / "src").mkdir(parents=True)
    (root / "src" / "pricing.py").write_text(FIXTURE, encoding="utf-8")

    with MetadataDB(db_path) as db:
        record = db.add_code_project(name="Testprojekt", path=str(root), kind="external")
    project_id = str(record["id"])

    router = FakeRouter([("apply_discount rechnet in src/pricing.py:1-2.", [])])
    monkeypatch.setattr(
        companion.LLMRouter, "from_config_file", staticmethod(lambda *_a, **_k: router)
    )

    with TestClient(pm.app) as client:
        client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

        with client.stream(
            "POST",
            f"/codegraph/{project_id}/ask",
            json={
                "question": "Was macht apply_discount?",
                "metadata_db_path": db_path,
            },
        ) as response:
            assert response.status_code == 200
            events = [
                json.loads(line[len("data: ") :])
                for line in response.iter_lines()
                if line.startswith("data: ")
            ]

        assert any(event["event"] == "activity" for event in events)
        answer = events[-1]["answer"]
        assert answer["verdict"] == "sound"
        assert answer["id"], "die geprüfte Antwort gehört in die Historie"

        listed = client.get(
            f"/codegraph/{project_id}/answers", params={"metadata_db_path": db_path}
        ).json()
        assert [item["id"] for item in listed["answers"]] == [answer["id"]]
        assert listed["answers"][0]["citations"][0]["status"] == "verified"

    fresh.close_all()


def test_trail_steps_keep_their_shape():
    step = TrailStep(path="src/a.py", line=3, reason="hier rein")
    assert step.verified is False
    assert step.node_id is None


# --- Erklärung eines einzelnen Symbols ---------------------------------------
# Kein `context_build` und keine Werkzeuge: das Symbol steht fest, `get_node`
# liefert Fakten und Quelltext, und genau dieses Nachschlagen ist die Lizenz zum
# Zitieren. Was hier geprüft wird, ist wieder nicht die Klugheit des Modells,
# sondern dass eine erfundene Belegstelle als solche ankommt.


def _node_id(record, name: str) -> str:
    from codegraph import service

    hits = service.query(record, "search_symbols", {"query": name})
    return next(hit["id"] for hit in hits if hit["name"] == name)


@needs_binary
def test_an_explanation_cites_only_what_it_was_shown(project):
    from codegraph import explain

    record, _root = project
    node_id = _node_id(record, "apply_discount")
    router = FakeRouter([("Rechnet den Rabatt in src/pricing.py:1-2 aus.", [])])

    answer = explain.explain(record, node_id, router=router, session="e1")

    assert answer["citations"], "die Antwort nennt eine Stelle"
    assert answer["citations"][0]["status"] == "verified"
    assert answer["verdict"] == "sound"
    assert answer["model"] == "fake-model"


@needs_binary
def test_an_explanation_of_an_invented_file_is_caught(project):
    from codegraph import explain

    record, _root = project
    node_id = _node_id(record, "apply_discount")
    router = FakeRouter([("Steht in src/gibt/es/nicht.py:42.", [])])

    answer = explain.explain(record, node_id, router=router, session="e2")

    assert answer["citations"][0]["status"] == "unknown_file"
    assert answer["verdict"] == "broken"


@needs_binary
def test_an_explanation_gets_the_source_without_asking_for_tools(project):
    """Der Unterschied zur Frage: hier wird nicht gesucht, sondern nachgeschlagen."""
    from codegraph import explain

    record, _root = project
    node_id = _node_id(record, "apply_discount")
    router = FakeRouter([("Kurz erklärt, siehe src/pricing.py:1.", [])])

    explain.explain(record, node_id, router=router, session="e3")

    messages, tools = router.calls[0]
    assert tools is None, "ohne Werkzeuge — der Kontext steht schon im Prompt"
    prompt = messages[-1]["content"]
    assert "apply_discount" in prompt
    assert "total" in prompt, "die Fakten samt Quelltext müssen im Prompt stehen"


@needs_binary
def test_an_empty_model_answer_is_a_failure_not_an_empty_explanation(project):
    from codegraph import explain

    record, _root = project
    node_id = _node_id(record, "apply_discount")
    router = FakeRouter([("   ", [])])

    with pytest.raises(RuntimeError, match="keine Erklärung"):
        explain.explain(record, node_id, router=router, session="e4")


# --- Der Chat: mehrere Züge, Trefferliste, Zitierlizenz ----------------------


@needs_binary
def test_the_hit_list_says_why_each_function_is_in_it(project):
    """Eine Liste ohne Begründung wäre eine Behauptung.

    „Zitiert" heisst, die Stelle steht belegt in der Antwort. „Vorab-suche"
    heisst nur, dass sie im Kontext lag — das Modell hat sie vielleicht nie
    gelesen. Beides gleich anzuzeigen wäre genau der Fehler, gegen den die
    Belegprüfung existiert.
    """
    record, _root = project
    router = FakeRouter([("apply_discount rechnet in src/pricing.py:1-2.", [])])
    answer, _events = _answer(record, router, broad=True, max_symbols=18)

    focus = answer["focus_nodes"]
    assert focus, "eine Feature-Frage muss Symbole zum Anklicken liefern"
    for entry in focus:
        assert entry["why"] in {"zitiert", "spur", "nachgeschlagen", "vorab-suche"}
        assert isinstance(entry["id"], str), "Knoten-IDs bleiben Hex-Strings"
        assert entry["path"]

    # Die belegte Stelle steht vor den bloss gesuchten.
    assert focus[0]["why"] == "zitiert", [entry["why"] for entry in focus]
    assert any(entry["name"] == "apply_discount" for entry in focus)


@needs_binary
def test_a_tool_lookup_lands_in_the_hit_list_as_looked_up(project):
    record, root = project
    hits = None
    from codegraph import service

    client = service.client_for(record)
    hits = client.call("search_symbols", {"query": "checkout"})
    node_id = hits[0]["id"]

    router = FakeRouter(
        [
            (None, [ToolCall(id="t1", name="get_node", arguments=f'{{"id": "{node_id}"}}')]),
            ("checkout ruft apply_discount auf.", []),
        ]
    )
    answer, _events = _answer(record, router)

    by_id = {entry["id"]: entry for entry in answer["focus_nodes"]}
    assert node_id in by_id, "was nachgeschlagen wurde, gehört auf die Liste"
    assert by_id[node_id]["why"] in {"nachgeschlagen", "zitiert", "spur"}
    assert str(root)  # Fixture wirklich benutzt


@needs_binary
def test_a_follow_up_may_still_quote_what_the_first_turn_looked_up(project):
    """Der Kern des Chats: im Gespräch wächst die Lizenz, sie springt nicht zurück."""
    record, _root = project
    claim = "Der Rabatt steht in src/pricing.py:1-2."

    first = FakeRouter([(claim, [])])
    answer_one, _ = _answer(record, first, question="Wie wird der Rabatt gerechnet?",
                            session="chat_x", broad=True)
    assert answer_one["citations"][0]["status"] == "verified"

    # Rückfrage: anderes Thema, aber dasselbe Gespräch.
    second = FakeRouter([(claim, [])])
    answer_two, _ = _answer(
        record,
        second,
        question="Und wer ruft das auf?",
        session="chat_x",
        history=[{"question": "Wie wird der Rabatt gerechnet?", "answer": claim}],
        broad=True,
    )
    assert answer_two["citations"][0]["status"] == "verified", (
        "das Modell hat diese Zeilen in Runde eins gesehen — sie als "
        "unbelegt zu melden wäre die Prüfung, die lügt"
    )

    # Der Verlauf steht im Prompt, gekürzt und ohne Werkzeugergebnisse.
    messages = second.calls[0][0]
    assert any(
        message["role"] == "assistant" and claim in (message.get("content") or "")
        for message in messages
    ), "die vorherige Antwort gehört in den Prompt"
    assert not any(message["role"] == "tool" for message in messages), (
        "Werkzeugergebnisse früherer Züge würden bei jeder Runde neu verarbeitet"
    )


@needs_binary
def test_a_chat_thread_is_persisted_with_its_turns_in_order(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

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
    (root / "src").mkdir(parents=True)
    (root / "src" / "pricing.py").write_text(FIXTURE, encoding="utf-8")

    with MetadataDB(db_path) as db:
        record = db.add_code_project(name="Testprojekt", path=str(root), kind="external")
    project_id = str(record["id"])

    router = FakeRouter(
        [
            ("apply_discount rechnet in src/pricing.py:1-2.", []),
            ("checkout ruft es in src/pricing.py:5-6 auf.", []),
        ]
    )
    monkeypatch.setattr(
        companion.LLMRouter, "from_config_file", staticmethod(lambda *_a, **_k: router)
    )

    with TestClient(pm.app) as client:
        client.post(f"/codegraph/{project_id}/index", params={"metadata_db_path": db_path})

        chat = client.post(
            f"/codegraph/{project_id}/chats", json={"metadata_db_path": db_path}
        ).json()
        chat_id = chat["id"]
        assert chat["session_key"], "ein Gespräch = eine Zitierlizenz"

        for question in ("Was macht apply_discount?", "Und wer ruft es auf?"):
            with client.stream(
                "POST",
                f"/codegraph/{project_id}/chats/{chat_id}/ask",
                json={"question": question, "metadata_db_path": db_path},
            ) as response:
                assert response.status_code == 200
                events = [
                    json.loads(line[len("data: ") :])
                    for line in response.iter_lines()
                    if line.startswith("data: ")
                ]
            assert events[-1]["event"] == "done", events[-1]

        body = client.get(
            f"/codegraph/{project_id}/chats/{chat_id}", params={"metadata_db_path": db_path}
        ).json()
        turns = body["turns"]
        assert [turn["ordinal"] for turn in turns] == [0, 1], "Reihenfolge kommt aus der DB"
        assert turns[0]["question"] == "Was macht apply_discount?"
        assert turns[0]["focus_nodes"], "die Trefferliste wird mitgespeichert"
        assert body["chat"]["title"] == "Was macht apply_discount?", (
            "der erste Zug benennt das Gespräch, sonst hiessen alle gleich"
        )

        # Die flache Antwortliste bleibt unberührt — sie hat andere Leser.
        answers = client.get(
            f"/codegraph/{project_id}/answers", params={"metadata_db_path": db_path}
        ).json()
        assert answers["answers"] == [], "der Chat schreibt nicht doppelt nach code_answers"

        deleted = client.request(
            "DELETE",
            f"/codegraph/{project_id}/chats/{chat_id}",
            params={"metadata_db_path": db_path},
        ).json()
        assert deleted["deleted"] is True

    fresh.close_all()


def test_a_cloud_model_is_marked_as_one():
    """`:cloud` verlässt den Rechner — das muss im Zug stehen, nicht nur im Picker."""
    from api.routers.codegraph import _is_remote_model

    assert _is_remote_model("deepseek-v4-flash:cloud") is True
    assert _is_remote_model("qwen3.5:9b") is False
    # Nicht am Namen hängen bleiben: ein lokales Modell darf "cloud" heissen.
    assert _is_remote_model("cloud-llama:7b") is False
    assert _is_remote_model(None) is False
