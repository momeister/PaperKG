import json
import pytest
from types import SimpleNamespace

from query.answer_contract import (
    DraftAnswer,
    DraftClaim,
    Check,
    checked_answer,
    render_claims,
    utf16,
)
from query.kg_retriever import Evidence, Source, SearchHit
from query.passages import split_passages, bm25, index_document
from parsing.marker_parser import ParsedDocument, PAGE_BREAK
from parsing.layout import PARSER_VERSION, spatial_blocks
from storage.metadata_db import MetadataDB


def ev(eid="ev1", text="Authors recommend 20–30% based on their experience."):
    return Evidence(
        "paper,with,commas",
        "passage",
        text,
        1,
        evidence_id=eid,
        metadata={"passage_id": eid, "document_fingerprint": "v1", "page": 2},
    )


def test_render_deduplicates_per_claim_and_uses_utf16():
    first, second = ev(), ev("ev2", "Another original supporting passage.")
    claim = DraftClaim(
        claim_id="stable",
        text="🧪 Die Autoren empfehlen 20–30% aus Erfahrung, z. B. für Signale.",
        evidence_ids=["ev1", "ev1", "ev2"],
        kind="author_recommendation",
    )
    check = Check(
        claim_id="stable",
        verdict="supported",
        supporting_evidence_ids=claim.evidence_ids,
        explanation="Authors' experience retained",
    )
    text, links, claims = render_claims(
        [claim],
        {"stable": check},
        {"ev1": first, "ev2": second},
        {"ev1": True, "ev2": True},
    )
    assert len(links) == 2
    assert text.count("[paper,with,commas]") == 1
    for link in links:
        assert (
            text.encode("utf-16-le")[
                link["citation_start"] * 2 : link["citation_end"] * 2
            ].decode("utf-16-le")
            == "[paper,with,commas]"
        )
        assert link["claim_id"] == "stable"
        assert link["context"] == claim.text
    assert claims[0]["end"] == utf16(text)


def test_unverified_claim_has_no_replacement_citation():
    claim = DraftClaim(
        claim_id="c",
        text="An experiment had 999 participants.",
        evidence_ids=["ev1"],
        kind="finding",
    )
    check = Check(
        claim_id="c",
        verdict="not_supported",
        supporting_evidence_ids=["ev1"],
        explanation="No experiment in review",
    )
    text, links, _ = render_claims([claim], {"c": check}, {"ev1": ev()}, {"ev1": True})
    assert text.startswith("Nicht belegt:")
    assert not links


def test_duplicate_evidence_does_not_inflate_hit_score():
    hit = SearchHit(Source("p"))
    hit.add_evidence(ev())
    hit.add_evidence(ev())
    assert len(hit.evidence) == 1
    assert hit.score == 1


class Router:
    def __init__(self, mode="supported"):
        self.calls = []
        self.mode = mode

    def provider_settings(self, provider):
        return SimpleNamespace(model="cloud-test", context_size=32768)

    def chat(self, messages, provider=None, overrides=None):
        self.calls.append((messages, provider, overrides))
        if "DraftAnswer" in messages[-1]["content"]:
            if self.mode == "invalid":
                return '{"claims":[{"claim_id":"x","text":"Wrong","evidence_ids":["invented"],"kind":"finding"}]}'
            return json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "one",
                            "text": (
                                "Authors recommend 20–30% based on their experience."
                                if self.mode != "numbers"
                                else "An experiment had 999 participants."
                            ),
                            "evidence_ids": ["ev1", "ev1"],
                            "kind": "author_recommendation",
                        }
                    ]
                }
            )
        payload = json.loads(messages[1]["content"])
        claim = payload["claims"][0]["claim"]
        if self.mode == "partial" and "recommend" in claim["text"]:
            verdict, correction = (
                "partially_supported",
                "The authors rely on their experience.",
            )
        else:
            verdict, correction = "supported", None
        return json.dumps(
            {
                "checks": [
                    {
                        "claim_id": claim["claim_id"],
                        "verdict": verdict,
                        "supporting_evidence_ids": ["ev1"],
                        "explanation": "Checked attribution and numbers.",
                        "corrected_text": correction,
                    }
                ]
            }
        )


def run(tmp_path, router):
    return checked_answer(
        question="What do the authors advise?",
        sources=[Source("paper,with,commas")],
        evidence=[ev(), ev()],
        router=router,
        provider="cloud",
        model="explicit-cloud",
        overrides=None,
        conversation_context=None,
        diagnostics={},
        db_path=str(tmp_path / "db.duckdb"),
        pdf_base_dir=str(tmp_path),
    )


def test_checked_answer_caches_judge_and_propagates_model(tmp_path):
    router = Router()
    answer = run(tmp_path, router)
    assert answer.claims_version == 1
    assert len(answer.evidence) == len(answer.citation_links) == 1
    assert answer.claims[0]["verification_status"] == "supported"
    assert answer.source_verification["sources"][0]["evidence"][0]["found_in_pdf_text"]
    assert len(router.calls) == 2
    assert all(
        provider == "cloud" and options["model"] == "explicit-cloud"
        for _, provider, options in router.calls
    )
    again = run(tmp_path, router)
    assert len(router.calls) == 3
    assert again.context_diagnostics["verification_cache_hits"] == 1


def test_invalid_draft_only_one_repair(tmp_path):
    router = Router("invalid")
    answer = run(tmp_path, router)
    assert len(router.calls) == 2
    assert answer.no_answer and answer.generation_error
    assert not answer.citation_links


def test_numbers_backstop_overrules_optimistic_judge(tmp_path):
    answer = run(tmp_path, Router("numbers"))
    assert answer.claims[0]["verification_status"] == "not_supported"
    assert not answer.citation_links


def test_partial_claim_corrected_once_rechecked_with_stable_id(tmp_path):
    router = Router("partial")
    answer = run(tmp_path, router)
    assert len(router.calls) == 3
    checks = [
        json.loads(messages[1]["content"])["claims"][0]["claim"]
        for messages, _, _ in router.calls[1:]
    ]
    assert checks[0]["claim_id"] == checks[1]["claim_id"]
    assert answer.claims[0]["verification_status"] == "supported"
    assert answer.answer.startswith("The authors rely on their experience.")


def test_passages_page_boundaries_stability_and_coverage():
    text = (
        "INTRODUCTION\n\n"
        + "one two three " * 400
        + PAGE_BREAK
        + "Recommendation: 20–30%."
    )
    rows = split_passages("p", text, "v1")
    assert rows == split_passages("p", text, "v1")
    assert {r["page"] for r in rows} == {1, 2}
    assert not {r["passage_id"] for r in rows}.intersection(
        r["passage_id"] for r in split_passages("p", text, "v2")
    )
    for row in rows:
        assert (
            text.split(PAGE_BREAK)[row["page"] - 1][row["start"] : row["end"]]
            == row["text"]
        )
        assert len(row["text"]) <= 1500


def test_spanning_heading_and_table_do_not_mix_columns():
    def w(text, x, y, width=30):
        return {"text": text, "x0": x, "x1": x + width, "top": y, "bottom": y + 10}

    words = [
        w("left-a", 30, 100),
        w("right-a", 330, 100),
        w("left-b", 30, 120),
        w("right-b", 330, 120),
        w("HEADING", 270, 160, 80),
        w("left-c", 30, 200),
        w("right-c", 330, 200),
        w("cell1", 100, 250),
        w("cell2", 340, 250),
    ]
    blocks = spatial_blocks(words, 600, 800, 300, [(90, 245, 400, 265)])
    text = "\n\n".join(b["text"] for b in blocks)
    assert (
        text.index("left-b")
        < text.index("right-a")
        < text.index("HEADING")
        < text.index("left-c")
    )
    assert sum(b["word_count"] for b in blocks) == len(words)
    assert blocks[-1]["kind"] == "table"
    for block in blocks:
        assert text[block["start"] : block["end"]] == block["text"]


def test_bm25_finds_specific_passage():
    rows = [
        {"paper_id": "p", "page": 1, "start_pos": i, "text": t}
        for i, t in enumerate(
            [
                "rapid adapting RA receptors",
                "Weber fraction amplitude discrimination",
                "unrelated information",
            ]
        )
    ]
    assert bm25(rows, "Weber fraction")[0][1]["start_pos"] == 1


def test_document_cache_invalidates_by_content(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"first")
    parsed = ParsedDocument(
        "p", "marker", "First source text.", 1, {"parser_version": PARSER_VERSION}
    )
    with MetadataDB(str(tmp_path / "db.duckdb")) as db:
        first = index_document(db, "p", path, parsed)
        path.write_bytes(b"second")
        parsed.text = "Changed source text."
        second = index_document(db, "p", path, parsed)
        assert first[0]["passage_id"] != second[0]["passage_id"]
        assert len(db.list_passages(["p"])) == 1
        assert not db.list_passages(["other"])


def test_filters_before_limits_and_latest_success(tmp_path):
    with MetadataDB(str(tmp_path / "db.duckdb")) as db:
        for pid in ["target", "other"]:
            db.insert_paper(
                {"id": pid, "source": "test", "source_id": pid, "title": pid}
            )
        assert db.list_papers(limit=1, paper_ids=["target"])[0]["id"] == "target"
        for text in ["old", "new"]:
            db.save_extraction_result(
                paper_id="target",
                llm_provider="fake",
                llm_model="fake",
                claims=[{"text": text}],
            )
        rows = db.list_extraction_results(
            limit=1, paper_ids=["target"], latest_successful=True
        )
        assert len(rows) == 1
        assert rows[0]["claims"][0]["text"] == "new"


def test_gutter_twelve_points_is_not_a_spanning_body_line():
    from parsing.layout import words_from_chars

    words = [
        {"text": "left", "x0": 50, "x1": 283, "top": 100, "bottom": 110},
        {"text": "right", "x0": 295, "x1": 526, "top": 100, "bottom": 110},
        {"text": "next-left", "x0": 50, "x1": 283, "top": 112, "bottom": 122},
        {"text": "next-right", "x0": 295, "x1": 526, "top": 112, "bottom": 122},
    ]
    blocks = spatial_blocks(words, 576, 783, 289)
    assert [b["kind"] for b in blocks] == ["left", "right"]
    assert blocks[0]["text"] == "left\nnext-left"
    # Font boxes overlap vertically, but baselines still belong to separate lines.
    chars = [
        {"text": "A", "x0": 10, "x1": 15, "top": 10, "bottom": 22, "size": 12},
        {"text": "B", "x0": 10, "x1": 15, "top": 20, "bottom": 32, "size": 12},
    ]
    assert [w["text"] for w in words_from_chars(chars)] == ["A", "B"]


def test_decimal_comma_is_same_quantity():
    from query.answer_contract import _numbers

    assert _numbers("4,7% und 0,10 µm") == _numbers("4.7% and 0.1 µm")


def test_stream_emits_progress_then_only_final_answer(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import answers

    def run(request, progress):
        progress("retrieval", "Searching")
        progress("verification", "Checking")
        return {"answer": "Final", "claims_version": 1}

    monkeypatch.setattr(answers, "_query_answer", run)
    app = FastAPI()
    app.include_router(answers.router)
    response = TestClient(app).post("/query/answer/stream", json={"question": "q"})
    assert response.status_code == 200
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["type"] for e in events] == ["progress", "progress", "answer"]
    assert events[-1]["answer"]["claims_version"] == 1


def test_stream_reports_errors_without_publishing_draft(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import answers

    def run(request, progress):
        progress("retrieval", "Searching")
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(answers, "_query_answer", run)
    app = FastAPI()
    app.include_router(answers.router)
    response = TestClient(app).post("/query/answer/stream", json={"question": "q"})
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["type"] for e in events] == ["progress", "error"]


def test_memory_database_is_isolated_and_does_not_create_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with MetadataDB(":memory:") as first, MetadataDB(":memory:") as second:
        first.insert_paper(
            {"id": "p", "source": "test", "source_id": "p", "title": "p"}
        )
        assert len(first.list_papers()) == 1
        assert second.list_papers() == []
    assert not (tmp_path / ":memory:").exists()


def test_graph_expands_only_one_hop_of_located_approved_relations():
    from query.passages import graph_expansion

    relations = [
        {
            "subject_id": "RA",
            "object_id": "spatial summation",
            "evidence_span": "RA has spatial summation",
            "review_status": "approved",
        },
        {
            "subject_id": "spatial summation",
            "object_id": "second hop",
            "evidence_span": "RA has spatial summation",
            "review_status": "approved",
        },
        {
            "subject_id": "RA",
            "object_id": "fabricated",
            "evidence_span": "Quote absent from the paper",
            "review_status": "approved",
        },
        {
            "subject_id": "RA",
            "object_id": "unreviewed",
            "evidence_span": "RA has spatial summation",
            "review_status": "pending",
        },
    ]
    rows = [{"paper_id": "p", "text": "RA has spatial summation in these conditions."}]
    assert graph_expansion("RA", [{"paper_id": "p", "relations": relations}], rows) == [
        "spatial summation"
    ]
    assert (
        graph_expansion("RA", [{"paper_id": "other", "relations": relations}], rows)
        == []
    )


def test_claim_cache_invalidates_text_version_and_settings():
    from query.answer_contract import _cache_key

    claim = DraftClaim(claim_id="a", text="Claim", evidence_ids=["ev1"], kind="finding")
    evidence = ev()
    key = _cache_key(claim, [evidence], "cloud", {"model": "one"})
    assert key != _cache_key(claim, [evidence], "cloud", {"model": "two"})
    evidence.metadata["document_fingerprint"] = "v2"
    assert key != _cache_key(claim, [evidence], "cloud", {"model": "one"})
    evidence.metadata["document_fingerprint"] = "v1"
    from dataclasses import replace

    evidence = replace(evidence, text=evidence.text + " Changed.")
    assert key != _cache_key(claim, [evidence], "cloud", {"model": "one"})


def test_reconstructible_caches_follow_paper_deletion_and_clear_all(tmp_path):
    with MetadataDB(":memory:") as db:
        db.insert_paper({"id": "p", "source": "test", "source_id": "p", "title": "p"})
        db.replace_passages(
            "p",
            "v",
            PARSER_VERSION,
            "p.pdf",
            split_passages("p", "Original passage.", "v"),
        )
        db.cache_claim_check("key", {"verdict": "supported"})
        assert db.delete_paper("p")
        assert db.list_passages() == []
        assert db.passage_document("p") is None
        db.clear_all()
        assert db.cached_claim_check("key") is None


def test_structured_preamble_is_discarded_without_changing_claims_or_evidence():
    from query.answer_contract import _parse_output

    draft = {
        "claims": [
            {
                "claim_id": "c",
                "text": "Claim with literal {braces}.",
                "evidence_ids": ["ev1"],
                "kind": "finding",
            }
        ]
    }
    raw = "The final response follows:\n" + json.dumps(draft)
    assert _parse_output(raw, DraftAnswer).model_dump() == draft


def test_ambiguous_or_nested_structured_outputs_are_not_guessed():
    from query.answer_contract import _parse_output

    draft = {
        "claims": [
            {
                "claim_id": "c",
                "text": "Claim.",
                "evidence_ids": ["ev1"],
                "kind": "finding",
            }
        ]
    }
    with pytest.raises(ValueError):
        _parse_output(
            "Two drafts: " + json.dumps(draft) + json.dumps(draft), DraftAnswer
        )
    with pytest.raises(ValueError):
        _parse_output(json.dumps({"unexpected_wrapper": draft}), DraftAnswer)
    with pytest.raises(ValueError):
        _parse_output(json.dumps(draft)[:-2], DraftAnswer)


@pytest.mark.parametrize(
    "metadata",
    [
        {"done_reason": "length"},
        {"finish_reason": "length"},
        {"stop_reason": "max_tokens"},
        {"reasoning_truncated": True},
        {"reasoning_fallback": True},
    ],
)
def test_unfinished_model_output_cannot_promote_embedded_valid_json(metadata):
    from query.answer_contract import _json_call

    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        # Reasoning may contain a complete example/draft even when the provider
        # never finished its answer. Schema validity alone is insufficient.
        return json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "c",
                        "text": "A draft.",
                        "evidence_ids": ["ev1"],
                        "kind": "finding",
                    }
                ]
            }
        )

    router = SimpleNamespace(chat=chat, last_response_metadata=metadata)
    with pytest.raises(ValueError, match="completed final response"):
        _json_call(
            router, [{"role": "user", "content": "Question"}], DraftAnswer, "cloud", {}
        )
    assert len(calls) == 2


def test_completed_repair_after_output_limit_can_be_validated():
    from query.answer_contract import _json_call

    router = SimpleNamespace(last_response_metadata={})
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        router.last_response_metadata = {
            "done_reason": "length" if len(calls) == 1 else "stop"
        }
        return json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "c",
                        "text": "A completed claim.",
                        "evidence_ids": ["ev1"],
                        "kind": "finding",
                    }
                ]
            }
        )

    router.chat = chat
    result = _json_call(
        router, [{"role": "user", "content": "Question"}], DraftAnswer, "cloud", {}
    )
    assert result.claims[0].text == "A completed claim."
    assert len(calls) == 2
