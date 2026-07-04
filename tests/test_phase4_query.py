from __future__ import annotations

import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from extraction.embedding_engine import EmbeddingEngine
from query.grounded_responder import (
    _CONTEXT_MATCH_SCORE,
    GroundedResponder,
    _best_citation_evidence,
    _build_grounded_prompt,
    _citation_links_for_answer,
    _extract_evidence_bindings,
    _map_numeric_citations,
    _parse_numbered_translations,
    _strip_invalid_citations,
)
from query.hybrid_retriever import HybridRetriever
from query.hypothesis_generator import HypothesisGenerator
from query.kg_retriever import Evidence, KGRetriever, SearchHit, Source
from storage.metadata_db import MetadataDB


@dataclass
class FakeSettings:
    model: str = "fake-model"


class FakeLLMRouter:
    def __init__(self) -> None:
        self.calls = []
        self.default_provider = "fake"

    def available_providers(self) -> list[str]:
        return ["fake"]

    def provider_settings(self, provider=None) -> FakeSettings:
        return FakeSettings()

    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return "Graph Transformer is represented in the local KG evidence [p1]."


class FailingLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        raise RuntimeError("model unavailable")


class TransientThenAnswerLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        if len(self.calls) == 1:
            raise RuntimeError("503 Service Unavailable: high demand")
        return "Recovered after transient provider failure [p1]."


class EmptyReasoningThenAnswerLLMRouter(FakeLLMRouter):
    def __init__(self) -> None:
        super().__init__()
        self.last_response_metadata = {}

    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        if len(self.calls) == 1:
            max_tokens = int((overrides or {}).get("max_tokens") or 0)
            self.last_response_metadata = {
                "finish_reason": "length",
                "usage": {
                    "completion_tokens_details": {
                        "reasoning_tokens": max(max_tokens - 1, 0),
                    }
                },
            }
            return ""
        self.last_response_metadata = {"finish_reason": "stop", "usage": {}}
        return "Recovered after larger reasoning budget [p1]."


class CapturingLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return "Captured evidence [clinical]."


class RepeatedPaperCitationLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return (
            "Graph transformers improve scientific paper linking [p1]. "
            "The method applies transformer attention to graph-structured scientific data [p1]."
        )


@contextmanager
def _phase4_fixture():
    root = Path("test-output") / f"phase4-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "metadata.duckdb")
    db = MetadataDB(db_path)
    try:
        db.insert_paper(
            {
                "id": "p1",
                "source": "fixture",
                "source_id": "p1",
                "title": "Graph Transformer for Scientific Discovery",
                "abstract": "A graph transformer method for linking paper concepts.",
                "year": 2024,
                "references": ["r1", "r2"],
                "citations": ["r1", "r2"],
                "landing_page_url": "https://example.test/p1",
            }
        )
        db.insert_paper(
            {
                "id": "p2",
                "source": "fixture",
                "source_id": "p2",
                "title": "Biology Transfer Learning",
                "abstract": "Uses shared references and representation learning.",
                "year": 2023,
                "references": ["r1", "r3"],
                "citations": ["r1", "r3"],
            }
        )
        db.insert_paper(
            {
                "id": "p3",
                "source": "fixture",
                "source_id": "p3",
                "title": "Survey Citing Graph Transformer Work",
                "abstract": "A survey that cites p1.",
                "year": 2025,
                "references": ["p1"],
                "citations": ["p1"],
            }
        )
        db.save_extraction_result(
            paper_id="p1",
            llm_provider="fake",
            llm_model="fake-model",
            paper_type="research",
            concepts=[
                {
                    "label": "Graph Transformer",
                    "context": "central architecture",
                    "confidence": 0.94,
                }
            ],
            methods=[
                {
                    "label": "Graph Transformer",
                    "domain": "machine learning",
                    "description": "Applies transformer attention to graph-structured scientific data.",
                }
            ],
            claims=[
                {
                    "statement": "Graph transformers improve scientific paper linking.",
                    "evidence_type": "experimental",
                }
            ],
            cross_domain_hints=[
                {
                    "field": "robotics",
                    "why_applicable": "Graph attention can transfer to robot task graphs.",
                }
            ],
        )
        engine = EmbeddingEngine()
        vector = engine.embed("Graph Transformer").tolist()
        db.upsert_entity_embedding(
            "Graph Transformer",
            vector,
            model=engine.model_name,
            backend=engine.backend,
            dimension=engine.EMBEDDING_DIM,
        )
        db.close()
        yield db_path
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_kg_retriever_searches_paper_metadata_and_extractions() -> None:
    with _phase4_fixture() as db_path:
        retriever = KGRetriever(metadata_db_path=db_path)

        hits = retriever.search("graph transformer", limit=5)

        assert hits
        assert hits[0].source.paper_id == "p1"
        assert any(item.kind == "method" for item in hits[0].evidence)
        assert any(item.kind == "claim" for item in hits[0].evidence)


def test_kg_retriever_resolves_pdf_derived_extraction_ids_to_metadata() -> None:
    root = Path("test-output") / f"phase4-alias-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "metadata.duckdb")
    db = MetadataDB(db_path)
    try:
        db.insert_paper(
            {
                "source": "arxiv",
                "source_id": "2306.04338",
                "title": "Changing Data Sources in the Age of Machine Learning for Official Statistics",
                "year": 2023,
            }
        )
        db.save_extraction_result(
            paper_id="arxiv__changing-data-sources-in-the-age-of-machine-learning-for-official-statistics__2306.04338",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[{"label": "Concept Drift", "confidence": 0.9}],
        )
        db.close()

        hits = KGRetriever(metadata_db_path=db_path).search("concept drift", limit=5)

        assert hits
        assert hits[0].source.paper_id == "arxiv:2306.04338"
        assert hits[0].source.year == 2023
        assert hits[0].evidence[0].metadata["raw_extraction_paper_id"].startswith("arxiv__changing")
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_kg_retriever_ignores_low_signal_query_matches_when_specific_terms_exist() -> None:
    root = Path("test-output") / f"phase4-specificity-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "metadata.duckdb")
    db = MetadataDB(db_path)
    try:
        db.insert_paper(
            {
                "id": "clinical",
                "source": "fixture",
                "source_id": "clinical",
                "title": "Clinical AI decision support",
                "abstract": "AI is used in primary care clinics for clinical error detection.",
                "year": 2025,
            }
        )
        db.insert_paper(
            {
                "id": "robot",
                "source": "fixture",
                "source_id": "robot",
                "title": "Robot AI planning",
                "abstract": "AI is used for robot planning and navigation.",
                "year": 2024,
            }
        )
        db.save_extraction_result(
            paper_id="clinical",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[{"label": "AI Consult", "context": "clinical safety net for clinics"}],
        )
        db.save_extraction_result(
            paper_id="robot",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[{"label": "AI planner", "context": "used for robot navigation"}],
        )
        db.close()

        hits = KGRetriever(metadata_db_path=db_path).search("How is ai used in clinics?", limit=5)

        assert hits
        assert hits[0].source.paper_id == "clinical"
        assert all(hit.source.paper_id != "robot" for hit in hits)
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_kg_retriever_diversifies_multi_domain_queries() -> None:
    root = Path("test-output") / f"phase4-diverse-aspects-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "metadata.duckdb")
    db = MetadataDB(db_path)
    try:
        db.insert_paper(
            {
                "id": "metamaterials",
                "source": "fixture",
                "source_id": "metamaterials",
                "title": "Programmable Metamaterials for Adaptive Surfaces",
                "abstract": "Metamaterials and metasurfaces tune physical properties with software commands.",
                "year": 2021,
            }
        )
        db.insert_paper(
            {
                "id": "robotics",
                "source": "fixture",
                "source_id": "robotics",
                "title": "Robot Adaptation in Contact",
                "abstract": "Robotics methods adapt control policies for manipulation.",
                "year": 2022,
            }
        )
        db.insert_paper(
            {
                "id": "ml",
                "source": "fixture",
                "source_id": "ml",
                "title": "Machine Learning Methods",
                "abstract": "Machine learning uses machine learning models for learning tasks.",
                "year": 2023,
            }
        )
        db.save_extraction_result(
            paper_id="ml",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[
                {"label": "Machine learning", "context": "machine learning model learning algorithm"},
                {"label": "Learning method", "context": "machine learning and model selection"},
            ],
        )
        db.close()

        hits = KGRetriever(metadata_db_path=db_path).search(
            "Are there ideas connecting metamaterials, robotics, and machine learning?",
            limit=3,
        )

        assert {hit.source.paper_id for hit in hits} == {"metamaterials", "robotics", "ml"}
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_kg_retriever_filters_to_allowed_paper_ids() -> None:
    with _phase4_fixture() as db_path:
        retriever = HybridRetriever(KGRetriever(metadata_db_path=db_path))

        hits = retriever.search("representation learning graph transformer", limit=5, paper_ids=["p2"])

        assert hits
        assert {hit.source.paper_id for hit in hits} == {"p2"}
        assert all(item.paper_id == "p2" for hit in hits for item in hit.evidence)


def test_kg_retriever_paper_detail_and_neighborhood() -> None:
    with _phase4_fixture() as db_path:
        retriever = KGRetriever(metadata_db_path=db_path)

        detail = retriever.paper_detail("p1")
        neighborhood = retriever.paper_neighborhood("p1")

        assert detail is not None
        assert detail["latest_extraction"]["paper_id"] == "p1"
        assert neighborhood is not None
        assert any(item["paper_id"] == "p3" for item in neighborhood["cited_by"])
        assert any(item["source"]["paper_id"] == "p2" for item in neighborhood["similar"])


def test_grounded_responder_uses_evidence_and_skips_empty_answers() -> None:
    with _phase4_fixture() as db_path:
        fake_llm = FakeLLMRouter()
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=fake_llm,
        )

        answer = responder.answer("What uses graph transformer?")
        missing = responder.answer("quantum annealing protein folding")

        assert answer.no_answer is False
        assert answer.sources[0].paper_id == "p1"
        assert "[p1]" in answer.answer
        assert missing.no_answer is True
        assert len(fake_llm.calls) == 1


def test_grounded_responder_skips_grey_source_injection_when_paper_ids_filter_is_set() -> None:
    with _phase4_fixture() as db_path:
        db = MetadataDB(db_path)
        try:
            db.add_grey_source(
                "proj1",
                {
                    "url": "https://example.test/grey1",
                    "title": "Grey Web Finding",
                    "evidence": ["A grey-source quote about graph transformers."],
                },
            )
        finally:
            db.close()

        fake_llm = FakeLLMRouter()
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=fake_llm,
        )

        # User scoped the answer to specific papers ("Auswahl" mode) - grey sources must
        # not be injected, otherwise the answer silently mixes in unrequested material.
        scoped = responder.answer(
            "What uses graph transformer?",
            paper_ids=["p1"],
            project_id="proj1",
            metadata_db_path=db_path,
        )
        assert "grey_source_count" not in scoped.context_diagnostics
        assert all(not source.paper_id.startswith("grey::") for source in scoped.sources)
        assert all(not item.paper_id.startswith("grey::") for item in scoped.evidence)

        # Without an explicit paper filter ("all sources"), grey sources are still
        # injected as supplementary citable evidence - existing behavior is preserved.
        unscoped = responder.answer(
            "What uses graph transformer?",
            project_id="proj1",
            metadata_db_path=db_path,
        )
        assert unscoped.context_diagnostics.get("grey_source_count") == 1


def test_grounded_responder_can_answer_from_pdf_context_if_it_fits(monkeypatch, tmp_path) -> None:
    from query import grounded_responder as responder_module

    class PdfScopedRetriever:
        def __init__(self) -> None:
            self.search_called = False

        def paper_detail(self, paper_id: str) -> dict:
            return {
                "source": {
                    "paper_id": paper_id,
                    "title": "Clinical AI Decision Support",
                    "year": 2025,
                    "url": "https://example.test/p1",
                }
            }

        def search(self, *args, **kwargs) -> list:
            self.search_called = True
            return []

    class VerificationResult:
        def to_dict(self) -> dict:
            return {"summary": {"valid_citation_count": 1}, "sources": [{"paper_id": "p1", "status": "verified"}]}

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    retriever = PdfScopedRetriever()
    fake_llm = FakeLLMRouter()
    monkeypatch.setattr(responder_module, "find_pdf_path", lambda *args, **kwargs: pdf_path)
    monkeypatch.setattr(
        responder_module,
        "parse_pdf_text",
        lambda *args, **kwargs: "Clinical AI reduces diagnostic errors in primary care.",
    )
    monkeypatch.setattr(responder_module, "verify_answer_sources", lambda *args, **kwargs: VerificationResult())

    answer = GroundedResponder(retriever=retriever, llm_router=fake_llm).answer(
        "What does the clinical AI paper report?",
        paper_ids=["p1"],
        answer_context_mode="pdf_if_fits",
        pdf_base_dir=str(tmp_path),
        overrides={"context_size": 32000, "max_tokens": 1200},
    )

    assert answer.no_answer is False
    assert answer.sources[0].paper_id == "p1"
    assert answer.context_diagnostics["answer_context_mode"] == "pdf_if_fits"
    assert answer.context_diagnostics["whole_context_used"] is True
    assert answer.source_verification["summary"]["valid_citation_count"] == 1
    assert retriever.search_called is False
    assert "Clinical AI reduces diagnostic errors" in fake_llm.calls[0]["messages"][1]["content"]


class TwoClaimPdfCitationLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return (
            "The trial reported a median overall survival of 16.8 months in the treatment group [p1]. "
            "Patients on the new treatment also experienced more fatigue and headaches than placebo [p1]."
        )


def test_pdf_context_answer_links_citations_to_distinct_claim_excerpts(monkeypatch, tmp_path) -> None:
    # Regression test for the "PDF-Assistent" bug where every [paper_id] citation in
    # pdf_if_fits mode pointed to the SAME generic, question-anchored snippet — which,
    # for papers whose title contains the topic terms (very common for clinical RCTs),
    # is just the title/author block. Each citation must instead resolve to the PDF
    # passage that actually supports its specific claim.
    from query import grounded_responder as responder_module

    class PdfScopedRetriever:
        def paper_detail(self, paper_id: str) -> dict:
            return {"source": {"paper_id": paper_id, "title": "Clinical Trial of NewDrug for Disease X", "year": 2025}}

        def search(self, *args, **kwargs) -> list:
            return []

    class VerificationResult:
        def to_dict(self) -> dict:
            return {"summary": {"valid_citation_count": 2}, "sources": [{"paper_id": "p1", "status": "verified"}]}

    pdf_text = (
        "Clinical Trial of NewDrug for Disease X. Jane Doe, MD, John Smith, PhD, and Alice Lee, MD. "
        "Abstract. Background: Disease X is a serious chronic condition affecting many patients worldwide "
        "and current treatment options remain limited in efficacy and tolerability for most affected individuals. "
        "Methods: We enrolled four hundred patients across twelve study centers and randomly assigned them "
        "to receive either the new treatment or a matching placebo for a period of eighteen months under blinded conditions. "
        "Results: The median overall survival was 16.8 months in the treatment group as compared with 11.2 months "
        "in the placebo group, a difference that was statistically significant according to the prespecified analysis "
        "plan for the primary endpoint. Safety analyses showed that adverse event rates were broadly comparable between "
        "groups during the early treatment period, though longer follow up will be needed to fully characterize the "
        "late safety profile of the new agent in this population. Patients in the treatment group reported more fatigue "
        "and headaches than those receiving placebo during routine follow-up visits, and these complaints were noted "
        "consistently across study sites and were generally managed with supportive care measures. Conclusions: NewDrug "
        "improved survival but increased certain adverse events, and further studies are warranted to confirm benefit."
    )

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    fake_llm = TwoClaimPdfCitationLLMRouter()
    monkeypatch.setattr(responder_module, "find_pdf_path", lambda *args, **kwargs: pdf_path)
    monkeypatch.setattr(responder_module, "parse_pdf_text", lambda *args, **kwargs: pdf_text)
    monkeypatch.setattr(responder_module, "verify_answer_sources", lambda *args, **kwargs: VerificationResult())

    answer = GroundedResponder(retriever=PdfScopedRetriever(), llm_router=fake_llm).answer(
        "What did the trial find?",
        paper_ids=["p1"],
        answer_context_mode="pdf_if_fits",
        pdf_base_dir=str(tmp_path),
        overrides={"context_size": 32000, "max_tokens": 1200},
    )

    assert len(answer.citation_links) == 2
    evidence_by_id = {item.evidence_id: item for item in answer.evidence}
    survival_evidence = evidence_by_id[answer.citation_links[0]["evidence_id"]]
    side_effect_evidence = evidence_by_id[answer.citation_links[1]["evidence_id"]]

    assert survival_evidence.evidence_id != side_effect_evidence.evidence_id
    assert survival_evidence.metadata.get("context_policy") == "claim_excerpt"
    assert side_effect_evidence.metadata.get("context_policy") == "claim_excerpt"

    # Each citation must link to the sentence that actually supports ITS claim …
    assert "16.8 months" in survival_evidence.text
    assert "fatigue and headaches" in side_effect_evidence.text
    # … not the generic title/author block (the symptom Moritz reported: useless
    # "Belege" excerpts that were mostly just author names).
    assert "Jane Doe" not in survival_evidence.text
    assert "Jane Doe" not in side_effect_evidence.text


class GermanClaimTranslationLLMRouter(FakeLLMRouter):
    # Reproduces Moritz's exact reported bug: a German answer citing the SAME English
    # paper twice for two semantically distinct claims (steroid/glucocorticoid use vs.
    # adverse-event rate). Cross-language word-overlap matching has nothing concrete to
    # anchor on — both citations previously collapsed onto the same wrong "quality of
    # life" decoy excerpt. This fake distinguishes the translation call (its prompt
    # contains the "Claim summaries" marker from `_translate_claims_for_pdf_matching`)
    # from the answer-generation call and returns canned, verbatim-anchorable English
    # rewrites for each German claim.
    ANSWER = (
        "Zudem war der Bedarf an Glukokortikoiden in der Bevacizumab-Gruppe geringer [files]. "
        "Die Rate an unerwünschten Ereignissen war in der Bevacizumab-Gruppe höher als in der Placebogruppe [files]."
    )
    TRANSLATIONS = (
        "1: Glucocorticoid use over the course of the study was lower among patients who received bevacizumab "
        "than among those who received placebo\n"
        "2: The overall incidence of adverse events of any grade during the study was higher among patients "
        "in the bevacizumab group than among those in the placebo group"
    )

    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        prompt = messages[-1]["content"] if messages else ""
        if "Claim summaries" in prompt:
            return self.TRANSLATIONS
        return self.ANSWER


def test_parse_numbered_translations_maps_numbered_lines_back_to_originals() -> None:
    originals = ["Erste Behauptung", "Zweite Behauptung", "Dritte Behauptung"]
    response = (
        "1: First claim rewritten\n"
        "2. Second claim, rewritten differently\n"
        "3) \"Third claim in quotes\"\n"
    )

    mapping = _parse_numbered_translations(response, originals)

    assert mapping == {
        "Erste Behauptung": "First claim rewritten",
        "Zweite Behauptung": "Second claim, rewritten differently",
        "Dritte Behauptung": "Third claim in quotes",
    }


def test_parse_numbered_translations_ignores_unparseable_blank_or_out_of_range_lines() -> None:
    originals = ["Erste Behauptung", "Zweite Behauptung"]
    response = (
        "Sure, here are the rewrites:\n"
        "1: The first claim, rewritten\n"
        "2:    \n"
        "5: Out of range, should be ignored\n"
        "not a numbered line at all\n"
    )

    mapping = _parse_numbered_translations(response, originals)

    # Line 2's rewrite is blank after stripping quotes/whitespace — skipped, so the
    # caller transparently falls back to the original `citation_context` for it.
    assert mapping == {"Erste Behauptung": "The first claim, rewritten"}


def test_pdf_context_answer_translates_non_english_claims_before_anchoring_citations(monkeypatch, tmp_path) -> None:
    # Regression test for Moritz's report: in the German "PDF-Assistent", two
    # DIFFERENT German claims about the same English paper ("Glukokortikoide" /
    # steroid use, and "unerwünschte Ereignisse" / adverse-event rate) both showed
    # the SAME wrong citation excerpt (a "quality of life" decoy sentence that
    # mentions neither). Root cause: `best_excerpt` matched on raw word-overlap
    # between the German claim text and the English PDF — German terms share no
    # tokens with English ones, so only ubiquitous drug/disease names (present
    # throughout the whole paper) "matched", landing both claims on the same
    # arbitrary window. Fix: translate each claim into the PDF's language before
    # anchoring (`_translate_claims_for_pdf_matching`) and require a concrete
    # anchor (`best_excerpt(..., strict=True)`). This test must show both German
    # citations resolving to DISTINCT, on-topic excerpts — not the decoy sentence.
    from query import grounded_responder as responder_module

    class PdfScopedRetriever:
        def paper_detail(self, paper_id: str) -> dict:
            return {"source": {"paper_id": paper_id, "title": "Bevacizumab plus Radiotherapy-Temozolomide for Glioblastoma", "year": 2025}}

        def search(self, *args, **kwargs) -> list:
            return []

    class VerificationResult:
        def to_dict(self) -> dict:
            return {"summary": {"valid_citation_count": 2}, "sources": [{"paper_id": "files", "status": "verified"}]}

    pdf_text = (
        "Bevacizumab plus Radiotherapy-Temozolomide for Glioblastoma. Jane Doe, MD, John Smith, PhD. "
        "We report the results of a phase 3 trial of bevacizumab plus radiotherapy-temozolomide as compared "
        "with placebo plus radiotherapy-temozolomide in patients with newly diagnosed glioblastoma. "
        "Quality of life: No clinically meaningful differences in the trajectory of baseline quality of life "
        "and performance status were observed with bevacizumab as compared with placebo during the study period. "
        "Steroid use: Glucocorticoid use over the course of the study was lower among patients who received "
        "bevacizumab than among those who received placebo, a finding that may reflect reduced cerebral edema "
        "in the bevacizumab group during the treatment period. "
        "Tolerability: The overall incidence of adverse events of any grade during the study was higher among "
        "patients in the bevacizumab group than among those in the placebo group, broadly consistent with the "
        "known safety profile of antiangiogenic agents in this setting. "
        "Conclusions: The addition of bevacizumab to radiotherapy-temozolomide did not significantly improve "
        "overall survival in patients with newly diagnosed glioblastoma."
    )

    pdf_path = tmp_path / "files.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    fake_llm = GermanClaimTranslationLLMRouter()
    monkeypatch.setattr(responder_module, "find_pdf_path", lambda *args, **kwargs: pdf_path)
    monkeypatch.setattr(responder_module, "parse_pdf_text", lambda *args, **kwargs: pdf_text)
    monkeypatch.setattr(responder_module, "verify_answer_sources", lambda *args, **kwargs: VerificationResult())

    answer = GroundedResponder(retriever=PdfScopedRetriever(), llm_router=fake_llm).answer(
        "Was berichtet die Studie über Sicherheit und Verträglichkeit?",
        paper_ids=["files"],
        answer_context_mode="pdf_if_fits",
        pdf_base_dir=str(tmp_path),
        overrides={"context_size": 32000, "max_tokens": 1200},
    )

    assert len(answer.citation_links) == 2
    evidence_by_id = {item.evidence_id: item for item in answer.evidence}
    glucocorticoid_evidence = evidence_by_id[answer.citation_links[0]["evidence_id"]]
    adverse_event_evidence = evidence_by_id[answer.citation_links[1]["evidence_id"]]

    # The two claims must resolve to DIFFERENT excerpts — not collapse onto the
    # same (wrong) "quality of life" decoy, as Moritz observed ("Z6" shown twice).
    assert glucocorticoid_evidence.evidence_id != adverse_event_evidence.evidence_id
    assert glucocorticoid_evidence.text != adverse_event_evidence.text

    assert "Glucocorticoid" in glucocorticoid_evidence.text
    assert "adverse events" not in glucocorticoid_evidence.text
    assert "quality of life" not in glucocorticoid_evidence.text

    assert "adverse events" in adverse_event_evidence.text
    assert "Glucocorticoid" not in adverse_event_evidence.text
    assert "quality of life" not in adverse_event_evidence.text

    # The translation call must have been issued with the PDF's language sample
    # and the distinct German claim contexts (not the raw answer-generation prompt).
    translation_calls = [call for call in fake_llm.calls if "Claim summaries" in call["messages"][-1]["content"]]
    assert len(translation_calls) == 1
    assert "Glukokortikoiden" in translation_calls[0]["messages"][-1]["content"]
    assert "unerwünschten Ereignissen" in translation_calls[0]["messages"][-1]["content"]


def test_grounded_responder_links_repeated_paper_citations_to_distinct_evidence() -> None:
    with _phase4_fixture() as db_path:
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=RepeatedPaperCitationLLMRouter(),
        )

        answer = responder.answer("What does the graph transformer paper claim and do?")
        evidence_by_id = {item.evidence_id: item for item in answer.evidence}

        assert len(answer.citation_links) == 2
        assert {link["paper_id"] for link in answer.citation_links} == {"p1"}
        assert answer.citation_links[0]["evidence_id"] != answer.citation_links[1]["evidence_id"]
        assert evidence_by_id[answer.citation_links[0]["evidence_id"]].kind == "claim"
        assert evidence_by_id[answer.citation_links[1]["evidence_id"]].kind == "method"
        assert all(isinstance(link["citation_start"], int) for link in answer.to_dict()["citation_links"])


def test_citation_links_break_exact_score_ties_with_distinct_evidence() -> None:
    # Both evidence items score identically against both citation contexts (same
    # `kind`, same `score`, no shared terms/phrases with either context) — an exact
    # tie that previously made the lowest-indexed evidence win both occurrences,
    # showing the same excerpt ("Z1") twice instead of distinct excerpts ("Z1"/"Z2").
    answer_text = (
        "Widgets improve throughput significantly [p1]. "
        "Gadgets reduce latency notably [p1]."
    )
    evidence = [
        Evidence(
            paper_id="p1",
            kind="claim",
            text="Researchers compiled a broad survey of annotation tooling conventions.",
            score=0.0,
            evidence_id="ev-a",
        ),
        Evidence(
            paper_id="p1",
            kind="claim",
            text="Engineers documented common patterns across deployment pipeline stages.",
            score=0.0,
            evidence_id="ev-b",
        ),
    ]

    links = _citation_links_for_answer(answer_text, evidence)

    assert len(links) == 2
    assert links[0]["score"] == links[1]["score"]
    assert links[0]["evidence_id"] != links[1]["evidence_id"]
    assert {links[0]["evidence_id"], links[1]["evidence_id"]} == {"ev-a", "ev-b"}


def test_strip_invalid_citations_removes_bibliography_numbers() -> None:
    known = frozenset({"arxiv:1234", "files"})
    text = (
        "Bevacizumab did not improve survival [arxiv:1234, 17]. "
        "Adverse events were higher [26, 29]. "
        "The local upload agrees [files]."
    )

    stripped = _strip_invalid_citations(text, known)

    assert "[arxiv:1234]" in stripped  # valid id kept, bibliography number 17 dropped
    assert "[files]" in stripped
    assert "[26, 29]" not in stripped  # pure-numeric bracket removed entirely
    assert "Adverse events were higher." in stripped  # sentence text preserved, dead bracket gone


def test_best_citation_evidence_prefers_exact_context_match() -> None:
    context = "Overall survival was not improved"
    evidence = [
        Evidence(
            paper_id="files",
            kind="pdf",
            text="Unrelated chunk that happens to share the numbers 17 22 29.",
            score=11.0,
            evidence_id="ev-wrong",
            metadata={"context": "A different sentence entirely"},
        ),
        Evidence(
            paper_id="files",
            kind="pdf",
            text="The located excerpt describing overall survival outcomes.",
            score=11.0,
            evidence_id="ev-right",
            metadata={"context": context},
        ),
    ]

    index, item, score = _best_citation_evidence("files", context, evidence)

    assert item is not None and item.evidence_id == "ev-right"
    assert index == 1
    assert score == _CONTEXT_MATCH_SCORE


def test_grounded_responder_surfaces_generation_failures() -> None:
    with _phase4_fixture() as db_path:
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=FailingLLMRouter(),
        )

        answer = responder.answer("What uses graph transformer?")

        assert answer.no_answer is False
        assert answer.generation_error == "model unavailable"
        assert "Evidence-only fallback" in answer.answer


def test_grounded_responder_retries_transient_generation_failures() -> None:
    with _phase4_fixture() as db_path:
        fake_llm = TransientThenAnswerLLMRouter()
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=fake_llm,
        )

        answer = responder.answer("What uses graph transformer?")

        assert answer.generation_error is None
        assert answer.answer == "Recovered after transient provider failure [p1]."
        assert len(fake_llm.calls) == 2


def test_grounded_responder_retries_empty_reasoning_only_responses() -> None:
    with _phase4_fixture() as db_path:
        fake_llm = EmptyReasoningThenAnswerLLMRouter()
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=fake_llm,
        )

        answer = responder.answer(
            "What uses graph transformer?",
            overrides={"max_tokens": 1200},
        )

        assert answer.generation_error is None
        assert answer.answer == "Recovered after larger reasoning budget [p1]."
        assert len(fake_llm.calls) == 2
        assert fake_llm.calls[1]["overrides"]["max_tokens"] > fake_llm.calls[0]["overrides"]["max_tokens"]


def test_grounded_responder_supplements_numeric_claims_for_top_hits() -> None:
    root = Path("test-output") / f"phase4-numeric-claims-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "metadata.duckdb")
    db = MetadataDB(db_path)
    try:
        db.insert_paper(
            {
                "id": "clinical",
                "source": "fixture",
                "source_id": "clinical",
                "title": "Clinical AI decision support",
                "abstract": "AI Consult is used in primary care clinics.",
                "year": 2025,
            }
        )
        db.save_extraction_result(
            paper_id="clinical",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[{"label": "AI Consult", "context": "clinical safety net"}],
            claims=[
                {
                    "statement": "Clinicians with access to AI Consult made 16% fewer diagnostic errors and 13% fewer treatment errors.",
                    "evidence_type": "experimental",
                }
            ],
        )
        db.close()

        fake_llm = CapturingLLMRouter()
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=fake_llm,
        )

        responder.answer("How is AI used in clinics?", limit=3)

        prompt = fake_llm.calls[0]["messages"][1]["content"]
        assert "16% fewer diagnostic errors" in prompt
        assert "13% fewer treatment errors" in prompt
        assert "cite the supporting paper IDs together in one bracket" in prompt
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_hypothesis_generator_uses_cross_domain_hints() -> None:
    with _phase4_fixture() as db_path:
        generator = HypothesisGenerator(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path))
        )

        hypotheses = generator.generate(paper_id="p1", limit=5)

        assert hypotheses
        assert hypotheses[0].sources[0].paper_id == "p1"
        assert "robotics" in hypotheses[0].statement.lower()


def test_phase4_api_endpoints(monkeypatch) -> None:
    with _phase4_fixture() as db_path:
        from api import phase4_main

        monkeypatch.setattr(phase4_main, "llm_router", FakeLLMRouter())
        client = TestClient(phase4_main.app)

        search_response = client.post(
            "/query/search",
            json={"query": "graph transformer", "metadata_db_path": db_path},
        )
        answer_response = client.post(
            "/query/answer",
            json={"question": "What uses graph transformer?", "metadata_db_path": db_path},
        )
        detail_response = client.get("/papers/p1", params={"metadata_db_path": db_path})
        neighborhood_response = client.get("/papers/p1/neighborhood", params={"metadata_db_path": db_path})
        verify_response = client.post(
            "/sources/verify-answer",
            json={
                "metadata_db_path": db_path,
                "answer": answer_response.json(),
                "parse_pdfs": False,
            },
        )
        health_response = client.get("/system/health-report", params={"metadata_db_path": db_path})
        benchmark_response = client.get("/quality/benchmark")

        assert search_response.status_code == 200
        assert search_response.json()["hits"][0]["source"]["paper_id"] == "p1"
        assert answer_response.status_code == 200
        assert answer_response.json()["sources"][0]["paper_id"] == "p1"
        assert detail_response.status_code == 200
        assert detail_response.json()["source"]["paper_id"] == "p1"
        assert neighborhood_response.status_code == 200
        assert neighborhood_response.json()["paper_id"] == "p1"
        assert verify_response.status_code == 200
        assert verify_response.json()["sources"][0]["paper_id"] == "p1"
        assert health_response.status_code == 200
        assert health_response.json()["metadata_db"]["paper_count"] == 3
        assert benchmark_response.status_code == 200
        assert benchmark_response.json()["summary"]["case_count"] >= 1


def test_map_numeric_citations_replaces_evidence_numbers_with_paper_ids() -> None:
    evidence = [
        Evidence(paper_id="arxiv:2501.00001", kind="claim", field="claims", text="Claim one", score=7.0),
        Evidence(paper_id="p2", kind="claim", field="claims", text="Claim two", score=6.0),
    ]
    text = "First finding [1]. Second finding [2]. Out of range [99]. Bibliography copy [17-22]."

    mapped = _map_numeric_citations(text, evidence)

    # The item number is kept as a `#N` binding suffix; _extract_evidence_bindings
    # strips it before the answer is shown.
    assert "[arxiv:2501.00001#1]" in mapped
    assert "[p2#2]" in mapped
    assert "[99]" in mapped
    assert "[17-22]" in mapped
    assert "[1]" not in mapped
    assert "[2]" not in mapped


def test_map_numeric_citations_resolves_z_labels_from_lm_studio_models() -> None:
    """Qwen via LM Studio copies the UI's Z-labels into brackets ([Z1]) — map them too."""
    evidence = [
        Evidence(paper_id="arxiv:2501.00001", kind="claim", field="claims", text="Claim one", score=7.0),
        Evidence(paper_id="p2", kind="claim", field="claims", text="Claim two", score=6.0),
    ]
    text = "First finding [Z1]. Second finding [z 2]. Unknown [Z99]."

    mapped = _map_numeric_citations(text, evidence)

    assert "[arxiv:2501.00001#1]" in mapped
    assert "[p2#2]" in mapped
    assert "[Z99]" in mapped


def test_build_grounded_prompt_instructs_evidence_item_binding() -> None:
    source = Source(paper_id="p1", title="Paper One", year=2024, doi=None, url=None)
    hit = SearchHit(source=source)
    item = Evidence(paper_id="p1", kind="claim", field="claims", text="Claim one", score=7.0)
    hit.add_evidence(item)

    prompt = _build_grounded_prompt("What?", [hit], [item])

    # The model must declare WHICH numbered evidence item each citation draws on...
    assert "append '#' plus the number" in prompt
    assert "[arxiv:2301.12345#3]" in prompt
    # ...while bare item numbers stay forbidden.
    assert "never cite evidence item numbers like [1] or [4]" in prompt


def test_extract_evidence_bindings_strips_suffix_and_binds() -> None:
    evidence = [
        Evidence(paper_id="p1", kind="claim", field="claims", text="Claim one", score=7.0),
        Evidence(paper_id="p1", kind="claim", field="claims", text="Claim two", score=6.0),
        Evidence(paper_id="arxiv:2501.00001", kind="claim", field="claims", text="Claim three", score=5.0),
    ]
    text = "Fact one [p1#2]. Fact two [p1#9, arxiv:2501.00001#3]. Fact three [p1]."

    cleaned, bindings = _extract_evidence_bindings(text, evidence)

    # The suffix never reaches the display text — including invalid ones.
    assert "#" not in cleaned
    assert "[p1]" in cleaned
    assert "[p1, arxiv:2501.00001]" in cleaned
    by_paper = {pid: ids for (pid, _context), ids in bindings.items()}
    # Valid binding: p1#2 -> second evidence item.
    assert by_paper["p1"] == [evidence[1].evidence_id]
    # arxiv:...#3 binds to item 3 (same paper).
    assert by_paper["arxiv:2501.00001"] == [evidence[2].evidence_id]
    # p1#9 is out of range: stripped, no binding for that occurrence.
    assert sum(len(ids) for ids in bindings.values()) == 2


def test_extract_evidence_bindings_rejects_paper_mismatch() -> None:
    evidence = [
        Evidence(paper_id="p1", kind="claim", field="claims", text="Claim one", score=7.0),
        Evidence(paper_id="p2", kind="claim", field="claims", text="Claim two", score=6.0),
    ]
    # p1#2 points at evidence item 2, which belongs to p2 — suffix stripped, no binding.
    cleaned, bindings = _extract_evidence_bindings("Fact [p1#2].", evidence)

    assert cleaned == "Fact [p1]."
    assert bindings == {}


def test_citation_links_prefer_model_binding_over_lexical() -> None:
    lexical_favorite = Evidence(
        paper_id="p1",
        kind="claim",
        field="claims",
        text="Vision language models produce detailed image descriptions in benchmarks.",
        score=7.0,
    )
    bound_target = Evidence(
        paper_id="p1",
        kind="claim",
        field="claims",
        text="BLIP generates less detailed captions than newer models in comparisons.",
        score=6.0,
    )
    evidence = [lexical_favorite, bound_target]
    answer_text = "Vision language models produce detailed image descriptions [p1]."
    # Without a binding, lexical overlap would pick `lexical_favorite`.
    baseline = _citation_links_for_answer(answer_text, evidence)
    assert baseline[0]["evidence_id"] == lexical_favorite.evidence_id

    context = baseline[0]["context"]
    bindings = {("p1", context): [bound_target.evidence_id]}
    links = _citation_links_for_answer(answer_text, evidence, model_bindings=bindings)

    assert links[0]["evidence_id"] == bound_target.evidence_id
    assert links[0]["evidence_index"] == 1
    assert links[0]["score"] == round(_CONTEXT_MATCH_SCORE, 4)
    assert links[0]["binding"] == "model"
    # "detailed" + "models" shared with the sentence -> plausible binding, no flag.
    assert links[0].get("approximate") is not True


def test_citation_links_flag_model_binding_without_content_overlap() -> None:
    unrelated = Evidence(
        paper_id="p1",
        kind="claim",
        field="claims",
        text="We previously hypothesized that manipulating mid-spatial frequency components matters.",
        score=6.0,
    )
    evidence = [unrelated]
    answer_text = "BLIP produces weaker image captions than newer systems [p1]."
    context = _citation_links_for_answer(answer_text, evidence)[0]["context"]

    links = _citation_links_for_answer(
        answer_text, evidence, model_bindings={("p1", context): [unrelated.evidence_id]}
    )

    assert links[0]["binding"] == "model"
    # Zero content overlap between sentence and bound item -> honest approximate flag.
    assert links[0].get("approximate") is True


def test_normalize_citation_brackets_converts_cjk_variants() -> None:
    from query.grounded_responder import _normalize_citation_brackets

    text = "Befund eins 【arxiv:2501.00001】 und Befund zwei ［p2］."
    normalized = _normalize_citation_brackets(text)

    assert "[arxiv:2501.00001]" in normalized
    assert "[p2]" in normalized
    assert "【" not in normalized


def test_llm_router_strips_reasoning_blocks() -> None:
    from query.llm_router import strip_reasoning_blocks

    answer = "<think>Let me reason about [1] and [2]...</think>The finding holds [arxiv:2501.00001]."
    assert strip_reasoning_blocks(answer) == "The finding holds [arxiv:2501.00001]."
    # Unterminated reasoning (token limit hit mid-thought) must not leak as answer text.
    assert strip_reasoning_blocks("<think>endless reasoning without close") == ""
    assert strip_reasoning_blocks("Plain answer [p1].") == "Plain answer [p1]."


class NumericCitationLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return "Graph transformers link scientific concepts across papers [1]."


def test_generate_answer_maps_numeric_citations_without_llm_repair() -> None:
    fake_llm = NumericCitationLLMRouter()
    responder = GroundedResponder(retriever=None, llm_router=fake_llm)
    source = Source(paper_id="arxiv:2501.00001", title="Paper One", year=2025, doi=None, url=None)
    hit = SearchHit(source=source)
    item = Evidence(
        paper_id="arxiv:2501.00001",
        kind="claim",
        field="claims",
        text="Graph transformers link scientific concepts across papers.",
        score=7.0,
    )
    hit.add_evidence(item)

    text, error, diagnostics, bindings = responder._generate_answer(
        question="What links concepts?",
        hits=[hit],
        evidence=[item],
        provider=None,
        model=None,
        overrides=None,
    )

    assert "[arxiv:2501.00001]" in text
    assert "[1]" not in text
    assert "#" not in text
    assert error is None
    # The numeric citation is resolved deterministically - no LLM repair round trip.
    assert len(fake_llm.calls) == 1
    assert diagnostics.get("uncited_sentence_count") == 0
    # The bare [1] declared WHICH evidence item was used — it becomes a binding.
    assert diagnostics.get("model_evidence_binding_count") == 1
    assert list(bindings.values()) == [[item.evidence_id]]


class UncitedThenCitedLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        if len(self.calls) == 1:
            return "Zebra migrations remain wholly mysterious to everyone involved somehow."
        return "Graph transformers link scientific concepts across papers [arxiv:2501.00001]."


def test_sparse_repair_fires_when_answer_has_zero_citations_and_few_sources() -> None:
    fake_llm = UncitedThenCitedLLMRouter()
    responder = GroundedResponder(retriever=None, llm_router=fake_llm)
    source = Source(paper_id="arxiv:2501.00001", title="Paper One", year=2025, doi=None, url=None)
    hit = SearchHit(source=source)
    item = Evidence(
        paper_id="arxiv:2501.00001",
        kind="claim",
        field="claims",
        text="Graph transformers link scientific concepts across papers.",
        score=7.0,
    )
    hit.add_evidence(item)

    text, error, diagnostics, _bindings = responder._generate_answer(
        question="What links concepts?",
        hits=[hit],
        evidence=[item],
        provider=None,
        model=None,
        overrides=None,
    )

    # Zero citations must trigger the sparse repair even with a single available source.
    assert "[arxiv:2501.00001]" in text
    assert len(fake_llm.calls) == 2
    assert error is None
    assert "fallback_reason" not in diagnostics


class AlwaysUncitedLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return "Zebra migrations remain wholly mysterious to everyone involved somehow."


def test_zero_citation_answer_falls_back_to_extractive_with_note() -> None:
    fake_llm = AlwaysUncitedLLMRouter()
    responder = GroundedResponder(retriever=None, llm_router=fake_llm)
    source = Source(paper_id="p1", title="Graph Paper", year=2024, doi=None, url=None)
    hit = SearchHit(source=source)
    item = Evidence(
        paper_id="p1",
        kind="claim",
        field="claims",
        text="Graph transformers link scientific concepts across papers.",
        score=7.0,
    )
    hit.add_evidence(item)

    text, error, diagnostics, _bindings = responder._generate_answer(
        question="What links concepts?",
        hits=[hit],
        evidence=[item],
        provider=None,
        model=None,
        overrides=None,
    )

    # Sentence attachment finds no overlap, so the answer is replaced by the
    # always-cited extractive fallback - never an uncited answer.
    assert text.startswith("Hinweis:")
    assert "[p1]" in text
    assert error is None
    assert diagnostics["fallback_reason"] == "no_traceable_citations"


def test_best_citation_evidence_does_not_steal_other_contexts_claim_excerpt() -> None:
    foreign = Evidence(
        paper_id="files",
        kind="pdf",
        field="answer_claim_excerpt",
        text="Patients in the treatment group reported more fatigue and headaches than those receiving placebo.",
        score=11.0,
        metadata={"context": "Anderer Satz ueber Muedigkeit", "context_policy": "claim_excerpt", "title": "Trial"},
    )
    whole = Evidence(
        paper_id="files",
        kind="pdf",
        field="parsed_pdf_text",
        text="Median overall survival was longer in the treatment group than in the placebo group.",
        score=10.0,
        metadata={"context_policy": "whole", "title": "Trial"},
    )
    # The citation context lexically overlaps the OTHER claim excerpt - previously that
    # foreign excerpt won the fallback and the wrong passage was displayed.
    context = "The treatment group reported more fatigue and headaches than placebo"

    index, best, score = _best_citation_evidence("files", context, [foreign, whole])

    assert best is not None
    assert best.metadata.get("context_policy") == "whole"
    assert index == 1
    assert score < _CONTEXT_MATCH_SCORE


class OneAnchoredOneUnanchoredPdfLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        prompt = messages[-1]["content"] if messages else ""
        if "Claim summaries" in prompt:
            return ""  # no usable translations -> claims are matched with their original wording
        return (
            "The median overall survival was 16.8 months in the treatment group [p1]. "
            "Zebras enjoy quantum knitting tournaments on Mars every winter [p1]."
        )


def test_pdf_context_keeps_whole_pdf_fallback_and_flags_unmatched_citation_links(monkeypatch, tmp_path) -> None:
    from query import grounded_responder as responder_module

    class PdfScopedRetriever:
        def paper_detail(self, paper_id: str) -> dict:
            return {"source": {"paper_id": paper_id, "title": "Clinical Trial of NewDrug", "year": 2025}}

        def search(self, *args, **kwargs) -> list:
            return []

    class VerificationResult:
        def to_dict(self) -> dict:
            return {"summary": {"valid_citation_count": 2}, "sources": []}

    pdf_text = (
        "Clinical Trial of NewDrug. Results: The median overall survival was 16.8 months in the "
        "treatment group as compared with 11.2 months in the placebo group according to the primary "
        "analysis. Safety findings were broadly comparable between the randomized study groups."
    )
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    fake_llm = OneAnchoredOneUnanchoredPdfLLMRouter()
    monkeypatch.setattr(responder_module, "find_pdf_path", lambda *args, **kwargs: pdf_path)
    monkeypatch.setattr(responder_module, "parse_pdf_text", lambda *args, **kwargs: pdf_text)
    monkeypatch.setattr(responder_module, "verify_answer_sources", lambda *args, **kwargs: VerificationResult())

    answer = GroundedResponder(retriever=PdfScopedRetriever(), llm_router=fake_llm).answer(
        "What did the trial find?",
        paper_ids=["p1"],
        answer_context_mode="pdf_if_fits",
        pdf_base_dir=str(tmp_path),
        overrides={"context_size": 32000, "max_tokens": 1200},
    )

    # The un-anchorable claim is counted and the paper keeps its whole-pdf fallback evidence.
    assert answer.context_diagnostics["unmatched_claim_context_count"] == 1
    policies = [item.metadata.get("context_policy") for item in answer.evidence]
    assert "claim_excerpt" in policies
    assert "whole" in policies

    assert len(answer.citation_links) == 2
    anchored_link, unmatched_link = answer.citation_links
    assert "approximate" not in anchored_link
    assert unmatched_link.get("approximate") is True
    # The unmatched citation links to the honest whole-pdf snippet, not to the other
    # claim excerpt.
    evidence_by_id = {item.evidence_id: item for item in answer.evidence}
    assert evidence_by_id[anchored_link["evidence_id"]].metadata.get("context_policy") == "claim_excerpt"
    assert "16.8" in evidence_by_id[anchored_link["evidence_id"]].text
    assert evidence_by_id[unmatched_link["evidence_id"]].metadata.get("context_policy") == "whole"


def test_grounded_prompt_marks_grey_evidence_as_web_source() -> None:
    paper_source = Source(paper_id="p1", title="Graph Paper", year=2024, doi=None, url=None)
    grey_source = Source(paper_id="grey::g1", title="Web Finding", year=None, doi=None, url="https://example.test")
    paper_hit = SearchHit(source=paper_source)
    grey_hit = SearchHit(source=grey_source)
    paper_item = Evidence(paper_id="p1", kind="claim", field="claims", text="Paper claim text.", score=7.0)
    grey_item = Evidence(
        paper_id="grey::g1",
        kind="quote",
        field="evidence",
        text="Grey quote text.",
        score=1.5,
        metadata={"source_type": "grey"},
    )
    paper_hit.add_evidence(paper_item)
    grey_hit.add_evidence(grey_item)

    prompt = _build_grounded_prompt("What?", [paper_hit, grey_hit], [paper_item, grey_item])

    lines = prompt.splitlines()
    grey_line = next(line for line in lines if "[grey::g1]" in line)
    paper_line = next(line for line in lines if "[p1]" in line and "Graph Paper" in line)
    assert "(Webquelle)" in grey_line
    assert "(Webquelle)" not in paper_line
    assert "more current than papers" in prompt
    # Papers stay the primary sources; web sources support them in the same bracket.
    assert "after the paper ID" in prompt


def test_answer_injects_selected_grey_sources_even_with_paper_ids() -> None:
    # "Auswahl" mode: explicitly selected grey sources arrive as IDs and must be injected
    # as citable grey:: sources (instead of the old anonymous "Inline-Kontext" blob),
    # even though an explicit paper filter is set.
    with _phase4_fixture() as db_path:
        db = MetadataDB(db_path)
        try:
            db.add_grey_source(
                "proj1",
                {
                    "id": "g_selected",
                    "url": "https://example.test/grey-selected",
                    "title": "Selected Web Finding",
                    "evidence": ["A grey-source quote about graph transformers."],
                    "full_text": "Long article body. Graph transformer adoption is accelerating in industry labs.",
                },
            )
        finally:
            db.close()

        fake_llm = FakeLLMRouter()
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=fake_llm,
        )
        answer = responder.answer(
            "What uses graph transformer?",
            paper_ids=["p1"],
            project_id="proj1",
            grey_source_ids=["g_selected"],
            metadata_db_path=db_path,
        )

        assert answer.context_diagnostics.get("selected_grey_count") == 1
        # The grey source must reach the LLM as citable (Webquelle) evidence — the final
        # answer.evidence is trimmed to whatever the (fake) LLM actually cited.
        prompt = fake_llm.calls[0]["messages"][1]["content"]
        assert "[grey::g_selected]" in prompt
        assert "(Webquelle)" in prompt


def test_answer_includes_project_grey_with_paper_filter_when_flag_set() -> None:
    # "Alle Quellen" scoped to the project's papers: include_project_grey keeps the
    # project's web findings as supplementary citable evidence despite the paper filter.
    with _phase4_fixture() as db_path:
        db = MetadataDB(db_path)
        try:
            db.add_grey_source(
                "proj1",
                {
                    "id": "g_project",
                    "url": "https://example.test/grey-project",
                    "title": "Project Web Finding",
                    "evidence": ["A grey-source quote about graph transformers."],
                },
            )
        finally:
            db.close()

        fake_llm = FakeLLMRouter()
        responder = GroundedResponder(
            retriever=HybridRetriever(KGRetriever(metadata_db_path=db_path)),
            llm_router=fake_llm,
        )
        answer = responder.answer(
            "What uses graph transformer?",
            paper_ids=["p1"],
            project_id="proj1",
            include_project_grey=True,
            metadata_db_path=db_path,
        )

        assert answer.context_diagnostics.get("grey_source_count") == 1
        prompt = fake_llm.calls[0]["messages"][1]["content"]
        assert "[grey::g_project]" in prompt


class ApproxRegionPdfLLMRouter(FakeLLMRouter):
    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        prompt = messages[-1]["content"] if messages else ""
        if "Claim summaries" in prompt:
            return ""
        # The 45% figure does NOT appear in the PDF -> strict anchoring fails, but the
        # surrounding topic terms do appear -> an approximate region must be shown.
        return "The study reported 45% fewer cognitive errors with memory recall training [p1]."


def test_pdf_context_falls_back_to_approx_region_for_unanchorable_numbers(monkeypatch, tmp_path) -> None:
    from query import grounded_responder as responder_module

    class PdfScopedRetriever:
        def paper_detail(self, paper_id: str) -> dict:
            return {"source": {"paper_id": paper_id, "title": "Cognitive Training Study", "year": 2025}}

        def search(self, *args, **kwargs) -> list:
            return []

    class VerificationResult:
        def to_dict(self) -> dict:
            return {"summary": {"valid_citation_count": 1}, "sources": []}

    pdf_text = (
        "Cognitive Training Study. Methods and procedures are described below in detail. "
        "Participants completed memory recall training over twelve weeks and made substantially "
        "fewer cognitive errors at follow-up compared with the control group in this study. "
        "Additional outcomes are reported in the appendix of this manuscript."
    )
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    fake_llm = ApproxRegionPdfLLMRouter()
    monkeypatch.setattr(responder_module, "find_pdf_path", lambda *args, **kwargs: pdf_path)
    monkeypatch.setattr(responder_module, "parse_pdf_text", lambda *args, **kwargs: pdf_text)
    monkeypatch.setattr(responder_module, "verify_answer_sources", lambda *args, **kwargs: VerificationResult())

    answer = GroundedResponder(retriever=PdfScopedRetriever(), llm_router=fake_llm).answer(
        "What did the study find?",
        paper_ids=["p1"],
        answer_context_mode="pdf_if_fits",
        pdf_base_dir=str(tmp_path),
        overrides={"context_size": 32000, "max_tokens": 1200},
    )

    assert answer.context_diagnostics.get("approx_region_context_count") == 1
    region_items = [
        item for item in answer.evidence if item.metadata.get("context_policy") == "approx_region"
    ]
    assert len(region_items) == 1
    assert "memory recall training" in region_items[0].text
    # The citation links to its own (approximate) region via the exact context match.
    assert answer.citation_links
    assert answer.citation_links[0]["evidence_id"] == region_items[0].evidence_id


_QUOTE_PDF_TEXT = (
    "Clinical Trial of NewDrug. Results: The median overall survival was 16.8 months in the "
    "treatment group as compared with 11.2 months in the placebo group according to the primary "
    "analysis. Patients in the treatment group reported more fatigue and headaches than placebo "
    "during routine follow-up visits across all participating study sites."
)


class ModelQuotePdfLLMRouter(FakeLLMRouter):
    ANSWER = (
        "Die Überlebenszeit war unter der Behandlung länger [p1]{{The median overall survival was "
        "16.8 months in the treatment group as compared with 11.2 months in the placebo group}}. "
        "Zudem traten mehr Nebenwirkungen auf [p1]{{Patients in the treatment group reported more "
        "fatigue and headaches than placebo}}."
    )

    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return self.ANSWER


def _pdf_context_answer(monkeypatch, tmp_path, fake_llm, pdf_text):
    from query import grounded_responder as responder_module

    class PdfScopedRetriever:
        def paper_detail(self, paper_id: str) -> dict:
            return {"source": {"paper_id": paper_id, "title": "Clinical Trial of NewDrug", "year": 2025}}

        def search(self, *args, **kwargs) -> list:
            return []

    class VerificationResult:
        def to_dict(self) -> dict:
            return {"summary": {}, "sources": []}

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(responder_module, "find_pdf_path", lambda *args, **kwargs: pdf_path)
    monkeypatch.setattr(responder_module, "parse_pdf_text", lambda *args, **kwargs: pdf_text)
    monkeypatch.setattr(responder_module, "verify_answer_sources", lambda *args, **kwargs: VerificationResult())
    return GroundedResponder(retriever=PdfScopedRetriever(), llm_router=fake_llm).answer(
        "What did the trial find?",
        paper_ids=["p1"],
        answer_context_mode="pdf_if_fits",
        pdf_base_dir=str(tmp_path),
        overrides={"context_size": 32000, "max_tokens": 1200},
    )


def test_pdf_context_anchors_citations_on_verified_model_quotes(monkeypatch, tmp_path) -> None:
    # The reliable path: the model appends the verbatim passage it used; the backend
    # verifies it character-for-character instead of re-locating a (German) paraphrase.
    fake_llm = ModelQuotePdfLLMRouter()

    answer = _pdf_context_answer(monkeypatch, tmp_path, fake_llm, _QUOTE_PDF_TEXT)

    assert "{{" not in answer.answer and "}}" not in answer.answer
    assert answer.context_diagnostics.get("model_quote_verbatim_count") == 2
    # Quotes resolve every context, so no translation LLM round trip happens.
    assert len(fake_llm.calls) == 1
    quote_items = [item for item in answer.evidence if item.metadata.get("anchor") == "model_quote"]
    assert len(quote_items) == 2
    assert any("16.8 months" in item.text for item in quote_items)
    assert any("fatigue and headaches" in item.text for item in quote_items)
    assert len(answer.citation_links) == 2
    linked_ids = {link["evidence_id"] for link in answer.citation_links}
    assert linked_ids == {item.evidence_id for item in quote_items}


class SharedQuotePdfLLMRouter(FakeLLMRouter):
    ANSWER = (
        "Die Überlebenszeit war unter der Behandlung länger [p1]{{The median overall survival was "
        "16.8 months in the treatment group as compared with 11.2 months in the placebo group}}. "
        "Der Unterschied war klinisch bedeutsam [p1]{{The median overall survival was 16.8 months "
        "in the treatment group as compared with 11.2 months in the placebo group}}."
    )

    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return self.ANSWER


def test_pdf_context_deduplicates_shared_excerpt_across_citations(monkeypatch, tmp_path) -> None:
    # Two answer sentences backed by the SAME passage must share one evidence entry
    # (with both contexts recorded) instead of listing duplicate quotes.
    fake_llm = SharedQuotePdfLLMRouter()

    answer = _pdf_context_answer(monkeypatch, tmp_path, fake_llm, _QUOTE_PDF_TEXT)

    claim_items = [item for item in answer.evidence if item.metadata.get("context_policy") == "claim_excerpt"]
    assert len(claim_items) == 1
    assert len(claim_items[0].metadata.get("contexts") or []) == 2
    assert len(answer.citation_links) == 2
    assert {link["evidence_id"] for link in answer.citation_links} == {claim_items[0].evidence_id}


class MultiQuotePdfLLMRouter(FakeLLMRouter):
    # One claim synthesized from TWO separate passages: the model appends both passages
    # as consecutive {{...}} blocks after the same citation bracket.
    ANSWER = (
        "Die Behandlung verlängerte das Überleben, verursachte aber mehr Nebenwirkungen [p1]"
        "{{The median overall survival was 16.8 months in the treatment group as compared "
        "with 11.2 months in the placebo group}}"
        "{{Patients in the treatment group reported more fatigue and headaches than placebo}}."
    )

    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        return self.ANSWER


def test_pdf_context_links_every_quoted_passage_of_one_citation(monkeypatch, tmp_path) -> None:
    # Moritz's report: a summary drawing on two PDF passages only ever showed ONE
    # Belegstelle. Both quoted passages must become evidence AND both must be linked
    # to the citation (same citation_start, two links).
    fake_llm = MultiQuotePdfLLMRouter()

    answer = _pdf_context_answer(monkeypatch, tmp_path, fake_llm, _QUOTE_PDF_TEXT)

    assert "{{" not in answer.answer and "}}" not in answer.answer
    assert answer.context_diagnostics.get("model_quote_verbatim_count") == 2
    quote_items = [item for item in answer.evidence if item.metadata.get("anchor") == "model_quote"]
    assert len(quote_items) == 2
    assert any("16.8 months" in item.text for item in quote_items)
    assert any("fatigue and headaches" in item.text for item in quote_items)

    assert len(answer.citation_links) == 2
    assert len({link["citation_start"] for link in answer.citation_links}) == 1
    assert {link["evidence_id"] for link in answer.citation_links} == {
        item.evidence_id for item in quote_items
    }


def test_citation_links_flag_zero_overlap_kg_evidence_as_approximate() -> None:
    # The claim-kind bonus alone used to bind a citation to an unrelated passage with a
    # confident look — multiple citations then displayed the same wrong excerpt. Links
    # without any shared term/number must carry the honest `approximate` flag.
    answer_text = "Quantum entanglement enables teleportation protocols [p1]."
    unrelated = [
        Evidence(
            paper_id="p1",
            kind="claim",
            text="Researchers surveyed annotation tooling conventions for biology labs.",
            score=5.0,
            evidence_id="ev-unrelated",
        )
    ]
    related = [
        Evidence(
            paper_id="p1",
            kind="claim",
            text="Quantum entanglement enables teleportation protocols across long distances.",
            score=5.0,
            evidence_id="ev-related",
        )
    ]

    unrelated_links = _citation_links_for_answer(answer_text, unrelated)
    related_links = _citation_links_for_answer(answer_text, related)

    assert unrelated_links[0]["evidence_id"] == "ev-unrelated"
    assert unrelated_links[0].get("approximate") is True
    assert related_links[0]["evidence_id"] == "ev-related"
    assert "approximate" not in related_links[0]
