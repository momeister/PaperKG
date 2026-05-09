from __future__ import annotations

import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from extraction.embedding_engine import EmbeddingEngine
from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever
from query.hypothesis_generator import HypothesisGenerator
from query.kg_retriever import KGRetriever
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

        assert search_response.status_code == 200
        assert search_response.json()["hits"][0]["source"]["paper_id"] == "p1"
        assert answer_response.status_code == 200
        assert answer_response.json()["sources"][0]["paper_id"] == "p1"
        assert detail_response.status_code == 200
        assert detail_response.json()["source"]["paper_id"] == "p1"
        assert neighborhood_response.status_code == 200
        assert neighborhood_response.json()["paper_id"] == "p1"
