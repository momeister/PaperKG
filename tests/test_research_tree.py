"""Tests for the deep-analysis research tree: synthesis citation stripping,
question de-duplication / deterministic resume, and real extraction of harvested papers.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from query import auto_harvester
from query.auto_harvester import _extract_pdf_into_db, harvest_for_question, ingest_paper_record
from query.research_tree import (
    ResearchTreeRunner,
    _normalize_question,
    _normalize_synthesis_body,
    _strip_unknown_citations,
)
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB


# --------------------------------------------------------------------------- #
# Fix 1 — _strip_unknown_citations must not raise "no such group"
# --------------------------------------------------------------------------- #

def test_strip_unknown_citations_keeps_known_and_strips_unknown() -> None:
    text = "Befund A [arxiv:1234.5678]. Behauptung B [arxiv:9999.0000]."
    out = _strip_unknown_citations(text, frozenset({"arxiv:1234.5678"}))
    # Known citation kept verbatim, unknown one removed — and crucially no IndexError.
    assert "[arxiv:1234.5678]" in out
    assert "[arxiv:9999.0000]" not in out


def test_strip_unknown_citations_passthrough_without_known_ids() -> None:
    text = "Etwas [arxiv:1]."
    assert _strip_unknown_citations(text, frozenset()) == text


# --------------------------------------------------------------------------- #
# Fix 2 — dedup of repeated questions + resume that does not re-search
# --------------------------------------------------------------------------- #

def _parse_events(raw_events: list[str]) -> list[dict]:
    return [json.loads(e.removeprefix("data: ").strip()) for e in raw_events]


def _make_runner(answer_calls: dict) -> ResearchTreeRunner:
    runner = ResearchTreeRunner(llm_router=object())  # type: ignore[arg-type]

    def fake_answer(question, *args, **kwargs):  # no self — set as instance attribute
        answer_calls["n"] += 1
        return {"answer": f"Antwort zu {question}", "no_answer": False, "context_diagnostics": {}, "sources": []}

    def fake_decompose(question, n, provider=None, model=None):
        # Same two sub-questions for every node → forces cross-branch duplicates.
        return ["Teilfrage A", "Teilfrage B"]

    runner._answer_sync = fake_answer  # type: ignore[assignment]
    runner._decompose_sync = fake_decompose  # type: ignore[assignment]
    runner._synthesize_sync = lambda *a, **k: ""  # type: ignore[assignment]
    return runner


async def test_research_tree_dedups_questions() -> None:
    answer_calls = {"n": 0}
    runner = _make_runner(answer_calls)

    raw = [e async for e in runner.stream_events(
        question="Hauptfrage", depth=2, branches=2, auto_harvest=False,
    )]
    done = [e for e in _parse_events(raw) if e.get("status") == "done"]

    questions = [_normalize_question(e["question"]) for e in done]
    # Each distinct question searched exactly once: root + A + B.
    assert sorted(questions) == ["hauptfrage", "teilfrage a", "teilfrage b"]
    assert len(questions) == len(set(questions))
    assert answer_calls["n"] == 3


async def test_research_tree_resume_does_not_reanswer() -> None:
    answer_calls = {"n": 0}
    runner = _make_runner(answer_calls)

    raw = [e async for e in runner.stream_events(
        question="Hauptfrage", depth=2, branches=2, auto_harvest=False,
    )]
    initial_nodes = [e for e in _parse_events(raw) if e.get("status") == "done"]

    answer_calls["n"] = 0
    raw2 = [e async for e in runner.stream_events(
        question="Hauptfrage", depth=2, branches=2, auto_harvest=False,
        initial_nodes=initial_nodes,
    )]
    done2 = [e for e in _parse_events(raw2) if e.get("status") == "done"]

    # Resume replays the saved tree without re-running a single answer LLM call.
    assert answer_calls["n"] == 0
    assert {_normalize_question(e["question"]) for e in done2} == {
        _normalize_question(e["question"]) for e in initial_nodes
    }


# --------------------------------------------------------------------------- #
# Synthesis depth — depth-2 sub-questions become their own ### subsections, and
# depth-3+ findings are folded into the matching subsection as context.
# --------------------------------------------------------------------------- #

def _tree_nodes() -> list[dict]:
    def node(nid, pid, q, depth, ans="...", sources=None):
        return {
            "id": nid, "parent_id": pid, "question": q, "depth": depth,
            "chapter_question": None if depth <= 1 else q,
            "answer": {"answer": ans, "sources": sources or []},
        }

    cited = [{"paper_id": "arxiv:1234.5678"}]
    return [
        node("r", None, "Hauptfrage", 0, "root-überblick", cited),
        node("c1", "r", "Kapitel Eins", 1, "kern eins", cited),
        node("c1a", "c1", "Unterfrage 1a", 2, "befund 1a"),
        node("c1b", "c1", "Unterfrage 1b", 2, "befund 1b"),
        node("c2", "r", "Kapitel Zwei", 1, "kern zwei"),
        node("c2a", "c2", "Unterfrage 2a", 2, "befund 2a"),
        node("c2a1", "c2a", "Tieferer Aspekt 2a1", 3, "tiefer befund 2a1"),
    ]


def test_synthesis_steps_promote_depth2_to_subsections() -> None:
    runner = ResearchTreeRunner(llm_router=object())  # type: ignore[arg-type]
    steps = runner._synthesis_steps(_tree_nodes(), "Hauptfrage")
    headings = [s["heading"] for s in steps]

    # Einleitung + 2 chapters + 3 depth-2 subsections + Fazit.
    assert headings[0] == "## Einleitung"
    assert headings[-1] == "## Fazit"
    assert "## Kapitel Eins" in headings
    assert "## Kapitel Zwei" in headings
    sub_headings = [h for h in headings if h.startswith("### ")]
    assert sub_headings == ["### Unterfrage 1a", "### Unterfrage 1b", "### Unterfrage 2a"]

    # Depth-3 finding is folded into its depth-2 parent's prompt, not its own section.
    sub_2a = next(s for s in steps if s["heading"] == "### Unterfrage 2a")
    assert "tiefer befund 2a1" in sub_2a["user"]


# --------------------------------------------------------------------------- #
# Synthesis body normalization — strip the LLM's restated heading (the cause of the
# duplicate-looking ToC entries) and demote/normalize stray ####/## headings.
# --------------------------------------------------------------------------- #

def test_normalize_strips_leading_restated_heading() -> None:
    # The LLM restates our injected "### {Frage}" as its own ### heading → must be dropped.
    body = "### Die Rolle des Systems\n\nText hier."
    out = _normalize_synthesis_body(body, "### Welche Rolle spielt das System?", keep_subsections=False)
    assert not out.startswith("#")
    assert out.startswith("Text hier.")


def test_normalize_demotes_stray_chapter_headings() -> None:
    body = "Absatz.\n\n## Neues Kapitel\n\nMehr Text."
    out_lines = _normalize_synthesis_body(body, "### Unterabschnitt", keep_subsections=False).split("\n")
    # Exact-line check (a "## …" substring would also match the demoted "#### …" line).
    assert "## Neues Kapitel" not in out_lines
    assert "#### Neues Kapitel" in out_lines


def test_normalize_keep_subsections_uses_h3_and_drops_h4_literals() -> None:
    body = "#### Erster Unterabschnitt\n\nText.\n\n## Zweiter\n\nText2."
    out = _normalize_synthesis_body(body, "## Kapitel", keep_subsections=True)
    assert "### Erster Unterabschnitt" in out
    assert "### Zweiter" in out
    assert "####" not in out  # h4 never leaks through in the flat-chapter step


def test_render_step_falls_back_to_node_answer_when_llm_empty() -> None:
    class EmptyRouter:
        def chat(self, messages, provider=None, overrides=None):
            return ""  # synthesis call yields nothing

    runner = ResearchTreeRunner(llm_router=EmptyRouter())  # type: ignore[arg-type]
    step = {
        "heading": "### Unterfrage",
        "system": "s",
        "user": "u",
        "fallback": "Belegter Befund [arxiv:1234.5678].",
    }
    out = runner._render_step(step, frozenset({"arxiv:1234.5678"}), None, None)
    # An answered question still renders content (the node's own answer), never an empty section.
    assert "### Unterfrage" in out
    assert "Belegter Befund" in out
    assert "[arxiv:1234.5678]" in out


def test_synthesize_sync_preserves_known_and_strips_unknown_citations() -> None:
    class FakeRouter:
        def chat(self, messages, provider=None, overrides=None):
            # Every section echoes one known and one unknown citation.
            return "Aussage [arxiv:1234.5678] und Behauptung [arxiv:9999.0000]."

    runner = ResearchTreeRunner(llm_router=FakeRouter())  # type: ignore[arg-type]
    doc = runner._synthesize_sync(_tree_nodes(), "Hauptfrage", None, None)

    assert "## Einleitung" in doc
    assert "### Unterfrage 1a" in doc
    assert "## Fazit" in doc
    # known paper id kept, unknown one removed by _strip_unknown_citations
    assert "[arxiv:1234.5678]" in doc
    assert "[arxiv:9999.0000]" not in doc


# --------------------------------------------------------------------------- #
# Fix 3 — real extraction of harvested papers + project attach
# --------------------------------------------------------------------------- #

def test_extract_pdf_into_db_writes_real_extraction(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"

    monkeypatch.setattr(
        "extraction.entity_extractor.extraction_failure_reason", lambda result: None
    )

    fake_result = SimpleNamespace(
        paper_type="empirical",
        concepts=[{"label": "Transformer", "description": "arch"}],
        methods=[],
        concept_candidates=[],
        method_candidates=[],
        relations=[],
        claims=[{"text": "It works."}],
        cross_domain_hints=[],
        terminology_conflicts=[],
        temporal_coverage={},
        mathematical_content={},
        raw_response="{}",
    )

    class FakePipeline:
        def process(self, paper_id, text, provider=None, overrides=None, link_concepts=True):
            return fake_result

    class FakeParser:
        def parse(self, pdf_path, paper_id, **kwargs):
            return SimpleNamespace(text="Long parsed paper text " * 20)

    with MetadataDB(str(db_path)) as db:
        db.insert_paper({"id": "arxiv:1", "source": "arxiv", "source_id": "1", "title": "T"})
        ok = _extract_pdf_into_db(
            db, FakePipeline(), FakeParser(), "arxiv:1", str(tmp_path / "x.pdf"),
            provider="lm_studio", model="qwen",
        )
        rows = db._execute(
            "SELECT llm_provider, extraction_status FROM extraction_results WHERE paper_id = ?",
            ["arxiv:1"],
        ).fetchall()

    assert ok is True
    assert len(rows) == 1
    # A real extraction is tagged with the actual provider, never the synthetic marker.
    assert rows[0][0] == "lm_studio"
    assert rows[0][0] != "auto-harvest"


async def test_harvest_attaches_to_project_and_synthetic_fallback(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    # Project file exists but has no entry for "demo" yet → attach must create it.
    projects_path.write_text(json.dumps({}), encoding="utf-8")

    paper = {
        "id": "arxiv:5", "source": "arxiv", "source_id": "5",
        "title": "Paper Five", "abstract": "An abstract.", "pdf_url": None,
    }

    class FakeArxiv:
        async def search(self, q, max_results=3):
            return [paper]

        async def close(self):
            return None

    class FakeSS:
        async def search_papers(self, q, limit=3, fields=""):
            return {"data": []}

        async def close(self):
            return None

    monkeypatch.setattr(auto_harvester, "ArxivClient", FakeArxiv)
    monkeypatch.setattr(auto_harvester, "SemanticScholarClient", FakeSS)

    inserted = await harvest_for_question(
        question="frage",
        project_id="demo",
        db_path=str(db_path),
        pdf_base_dir=str(pdf_dir),
        projects_path=str(projects_path),
        llm_router=None,  # no router → synthetic fallback, no LLM/network
    )

    assert [r["id"] for r in inserted] == ["arxiv:5"]
    saved = json.loads(projects_path.read_text(encoding="utf-8"))
    assert saved["demo"] == ["arxiv:5"]

    with MetadataDB(str(db_path)) as db:
        rows = db._execute(
            "SELECT llm_provider FROM extraction_results WHERE paper_id = ?", ["arxiv:5"],
        ).fetchall()
    # No PDF + no router → synthetic title/abstract extraction.
    assert rows and rows[0][0] == "auto-harvest"


# --------------------------------------------------------------------------- #
# On-demand ingest — download a cited paper's PDF and record the local path so the
# citation no longer reports pdf_available:false (the "Kein PDF verfügbar" limbo).
# --------------------------------------------------------------------------- #

class _FakePdfResponse:
    content = b"%PDF-1.4 minimal pdf bytes"
    headers = {"content-type": "application/pdf"}

    def raise_for_status(self) -> None:
        return None


class _FakePdfClient:
    async def get(self, url, follow_redirects=True, timeout=30.0):
        return _FakePdfResponse()


async def test_ingest_paper_record_downloads_and_records_local_pdf(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    storage = FileManager(str(pdf_dir))

    paper = {
        "id": "arxiv:7", "source": "arxiv", "source_id": "7",
        "title": "Paper Seven", "abstract": "Abstract.",
        "pdf_url": "https://example.org/seven.pdf",  # no DOI → OA resolver is a no-op
    }

    with MetadataDB(str(db_path)) as db:
        db.insert_paper(paper)
        result = await ingest_paper_record(
            paper, db, storage, _FakePdfClient(), extract=False,  # type: ignore[arg-type]
        )
        stored = db.get_paper("arxiv:7")

    assert result["has_local_pdf"] is True
    assert result["pdf_path"]
    # The DB row now points at the downloaded local PDF, so has_local_pdf resolves later.
    assert stored is not None
    assert str(stored.get("pdf_url") or "").endswith(".pdf")
    assert stored["pdf_url"] != paper["pdf_url"]  # local path, not the remote URL
