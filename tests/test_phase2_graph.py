from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from graph.citation_analysis import (
    build_co_citation_similarity,
    compute_obsolescence_score,
)
from graph.paper_ingestion import (
    extract_citation_ids,
    ingest_extractions_from_metadata_db,
    ingest_from_metadata_db,
    ingest_records,
    to_phase2_paper_node,
    to_reference_stub_paper_node,
)
from graph.project_global_merge import merge_project_records_into_global


class FakeGraph:
    def __init__(self) -> None:
        self.papers = {}
        self.citations = []
        self.similarities = []
        self.concepts = {}
        self.methods = {}
        self.has_concepts = []
        self.has_methods = []
        self.related_concepts = []

    def merge_paper(self, paper: dict) -> None:
        self.papers[paper["id"]] = paper

    def merge_citation(self, from_paper_id: str, to_paper_id: str) -> None:
        self.citations.append((from_paper_id, to_paper_id))

    def merge_similarity(self, from_paper_id: str, to_paper_id: str, score: float, similarity_type: str) -> None:
        self.similarities.append((from_paper_id, to_paper_id, score, similarity_type))

    def merge_concept(self, concept: dict) -> None:
        self.concepts[concept["id"]] = concept

    def merge_method(self, method: dict) -> None:
        self.methods[method["id"]] = method

    def merge_has_concept(
        self,
        paper_id: str,
        concept_id: str,
        weight: float,
        relation: str = "MENTIONS",
        evidence_span: str = "",
        confidence: float = 0.0,
        source: str = "",
    ) -> None:
        self.has_concepts.append((paper_id, concept_id, weight, relation, evidence_span, confidence, source))

    def merge_has_method(
        self,
        paper_id: str,
        method_id: str,
        weight: float,
        relation: str = "USES",
        evidence_span: str = "",
        confidence: float = 0.0,
        source: str = "",
    ) -> None:
        self.has_methods.append((paper_id, method_id, weight, relation, evidence_span, confidence, source))

    def merge_related_concept(self, subject_id: str, object_id: str, relation_type: str) -> None:
        self.related_concepts.append((subject_id, object_id, relation_type))


def test_extract_citation_ids_from_multiple_formats() -> None:
    record = {
        "references": [
            "arxiv:1",
            {"paperId": "S2:2"},
            {"doi": "10.1000/test"},
            {"id": "W3"},
        ]
    }

    citations = extract_citation_ids(record)

    assert citations == ["arxiv:1", "S2:2", "10.1000/test", "W3"]


def test_ingest_records_writes_papers_and_citations() -> None:
    graph = FakeGraph()
    records = [
        {
            "source": "arxiv",
            "source_id": "1234.5678",
            "title": "Paper A",
            "year": 2022,
            "references": ["arxiv:1111.1111", "arxiv:2222.2222"],
            "version": 2,
            "pdf_url": "https://arxiv.org/pdf/1234.5678v2",
        }
    ]

    stats = ingest_records(graph, records)

    assert stats.papers_seen == 1
    assert stats.papers_written == 1
    assert stats.citation_edges_written == 2
    assert "arxiv:1234.5678" in graph.papers
    assert "arxiv:1111.1111" in graph.papers
    assert graph.papers["arxiv:1111.1111"]["source"] == "citation_reference"


def test_ingest_from_metadata_db_uses_persisted_references(tmp_path) -> None:
    from storage.metadata_db import MetadataDB

    db = MetadataDB(str(tmp_path / "metadata.duckdb"))
    db.insert_paper(
        {
            "source": "arxiv",
            "source_id": "1234.5678",
            "title": "Paper A",
            "references": [{"paperId": "S2:1"}, "doi:10.1000/x"],
        }
    )
    graph = FakeGraph()

    stats = ingest_from_metadata_db(graph, db)

    assert stats.citation_edges_written == 2
    assert ("arxiv:1234.5678", "S2:1") in graph.citations
    db.close()


def test_ingest_extractions_writes_semantic_edges_from_canonical_paper() -> None:
    from storage.metadata_db import MetadataDB

    root = Path("test-output") / f"phase2-semantic-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db = MetadataDB(str(root / "metadata.duckdb"))
    try:
        db.insert_paper(
            {
                "source": "arxiv",
                "source_id": "2509.08759",
                "title": "A Very Real Paper Title With Punctuation",
                "year": 2025,
            }
        )
        db.save_extraction_result(
            paper_id="arxiv__a-very-real-paper-title-with-punctuation__2509.08759",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[
                {"label": "Concept Drift", "confidence": 0.91, "review_status": "approved"},
                {"label": "Concept Drift", "confidence": 0.42, "review_status": "approved"},
                {"label": ""},
            ],
            methods=[
                {
                    "label": "Data Source Monitoring",
                    "confidence": 0.8,
                    "review_status": "approved",
                    "source_type": "paper_contribution",
                    "evidence_span": "monitoring changes in data sources",
                }
            ],
            concept_candidates=[{"label": "Machine Learning", "confidence": 0.9}],
            method_candidates=[{"label": "Q-learning", "confidence": 0.9}],
        )
        graph = FakeGraph()

        stats = ingest_extractions_from_metadata_db(graph, db)

        assert stats.concept_edges_written == 1
        assert stats.method_edges_written == 1
        assert graph.has_concepts[0][0] == "arxiv:2509.08759"
        assert graph.has_methods[0][0] == "arxiv:2509.08759"
        assert graph.has_methods[0][3] == "INTRODUCES"
        assert graph.has_methods[0][4] == "monitoring changes in data sources"
        assert next(iter(graph.concepts.values()))["label"] == "Concept Drift"
        assert "Machine Learning" not in {node["label"] for node in graph.concepts.values()}
        assert "Q-learning" not in {node["label"] for node in graph.methods.values()}
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_ingest_extractions_filters_legacy_deterministic_scan_noise() -> None:
    class Metadata:
        def list_extraction_results(self, limit: int = 5000):
            return [
                {
                    "extraction_status": "success",
                    "paper_id": "paper_001",
                    "concepts": [
                        {"label": "Accepted Concept", "confidence": 0.91, "review_status": "approved"},
                        {
                            "label": "Deterministic Noise",
                            "confidence": 0.93,
                            "review_status": "approved",
                            "candidate_source": "deterministic_scan",
                        },
                        {"label": "Low Confidence", "confidence": 0.50, "review_status": "approved"},
                    ],
                    "methods": [
                        {"label": "Accepted Method", "confidence": 0.8, "review_status": "approved"},
                        {"label": "Auto Method", "confidence": 0.9, "review_status": "approved", "auto_detected": True},
                    ],
                }
            ]

        def resolve_paper(self, paper_id: str):
            return {"source": "arxiv", "source_id": "1234.5678", "title": "Paper"}

    graph = FakeGraph()

    stats = ingest_extractions_from_metadata_db(graph, Metadata())

    assert stats.concept_edges_written == 1
    assert stats.method_edges_written == 1
    assert {node["label"] for node in graph.concepts.values()} == {"Accepted Concept"}
    assert {node["label"] for node in graph.methods.values()} == {"Accepted Method"}


def test_ingest_extractions_creates_stub_paper_anchor_when_metadata_is_missing() -> None:
    class Metadata:
        def list_extraction_results(self, limit: int = 5000):
            return [
                {
                    "extraction_status": "success",
                    "paper_id": "paper_only_in_extractions",
                    "paper_type": "survey",
                    "concept_candidates": [],
                    "method_candidates": [],
                    "concepts": [],
                    "methods": [
                        {
                            "label": "Survey Framework",
                            "confidence": 0.82,
                            "review_status": "approved",
                            "source_type": "paper_contribution",
                            "evidence_span": "we introduce a survey framework",
                        }
                    ],
                    "temporal_coverage": {"paper_year": 2024},
                }
            ]

        def resolve_paper(self, paper_id: str):
            return None

    graph = FakeGraph()

    stats = ingest_extractions_from_metadata_db(graph, Metadata())

    assert stats.papers_written == 1
    assert "paper_only_in_extractions" in graph.papers
    assert graph.has_methods[0][0] == "paper_only_in_extractions"
    assert graph.has_methods[0][3] == "INTRODUCES"


def test_ingest_extractions_blocks_pending_review_entities() -> None:
    class Metadata:
        def list_extraction_results(self, limit: int = 5000):
            return [
                {
                    "extraction_status": "success",
                    "paper_id": "paper_001",
                    "concept_candidates": [],
                    "method_candidates": [],
                    "concepts": [
                        {
                            "label": "Pending Concept",
                            "confidence": 0.99,
                            "review_status": "pending",
                            "accepted": True,
                        },
                        {
                            "label": "Approved Concept",
                            "confidence": 0.8,
                            "review_status": "approved",
                            "canonical_id": "concept:approved",
                        },
                    ],
                    "methods": [
                        {
                            "label": "Pending Method",
                            "confidence": 0.99,
                            "review_status": "pending",
                            "accepted": True,
                        }
                    ],
                }
            ]

        def resolve_paper(self, paper_id: str):
            return {
                "id": paper_id,
                "title": "Paper",
                "source": "test",
            }

    graph = FakeGraph()

    stats = ingest_extractions_from_metadata_db(graph, Metadata())

    assert stats.concept_edges_written == 1
    assert stats.method_edges_written == 0
    assert {node["label"] for node in graph.concepts.values()} == {"Approved Concept"}
    assert next(iter(graph.concepts.values()))["id"] == "concept:approved"


def test_ingest_extractions_writes_only_approved_concept_relations() -> None:
    class Metadata:
        def list_extraction_results(self, limit: int = 5000):
            return [
                {
                    "extraction_status": "success",
                    "paper_id": "paper_001",
                    "concept_candidates": [],
                    "method_candidates": [],
                    "concepts": [
                        {
                            "label": "Appraisal dimensions",
                            "canonical_id": "concept:appraisal-dimensions",
                            "review_status": "approved",
                            "confidence": 0.9,
                        },
                        {
                            "label": "Appraisal theory",
                            "canonical_id": "concept:appraisal-theory",
                            "review_status": "approved",
                            "confidence": 0.9,
                        },
                    ],
                    "methods": [],
                    "relations": [
                        {
                            "subject_id": "concept:appraisal-dimensions",
                            "relation_type": "RELATED_TO",
                            "object_id": "concept:appraisal-theory",
                            "evidence_span": "appraisal dimensions in appraisal theory",
                            "review_status": "approved",
                        },
                        {
                            "subject_id": "concept:appraisal-theory",
                            "relation_type": "RELATED_TO",
                            "object_id": "concept:missing",
                            "evidence_span": "missing object",
                            "review_status": "approved",
                        },
                    ],
                }
            ]

        def resolve_paper(self, paper_id: str):
            return {"id": paper_id, "title": "Paper", "source": "test"}

    graph = FakeGraph()

    stats = ingest_extractions_from_metadata_db(graph, Metadata())

    assert stats.relation_edges_written == 1
    assert graph.related_concepts == [
        ("concept:appraisal-dimensions", "concept:appraisal-theory", "RELATED_TO")
    ]


def test_ingest_extractions_avoids_concept_method_duplicate_ids() -> None:
    class Metadata:
        def list_extraction_results(self, limit: int = 5000):
            return [
                {
                    "extraction_status": "success",
                    "paper_id": "paper_001",
                    "concept_candidates": [],
                    "method_candidates": [],
                    "concepts": [
                        {
                            "label": "Bayesian Affect Control Theory",
                            "canonical_id": "concept:bayesian-affect-control-theory",
                            "review_status": "approved",
                            "confidence": 0.9,
                        }
                    ],
                    "methods": [
                        {
                            "label": "Bayesian Affect Control Theory",
                            "canonical_id": "concept:bayesian-affect-control-theory",
                            "review_status": "approved",
                            "confidence": 0.9,
                        }
                    ],
                }
            ]

        def resolve_paper(self, paper_id: str):
            return {"id": paper_id, "title": "Paper", "source": "test"}

    graph = FakeGraph()

    stats = ingest_extractions_from_metadata_db(graph, Metadata())

    assert stats.concept_edges_written == 1
    assert stats.method_edges_written == 0


def test_to_phase2_paper_node_defaults() -> None:
    node = to_phase2_paper_node(
        {
            "source": "openalex",
            "source_id": "W1",
            "title": "Paper",
            "year": 2019,
            "references": [],
        }
    )

    assert node["id"] == "openalex:W1"
    assert node["version"] == 1
    assert node["confidence_score"] == 0.5
    assert node["obsolescence_score"] > 0


def test_reference_stub_node_defaults() -> None:
    node = to_reference_stub_paper_node("doi:10.1000/x")

    assert node["id"] == "doi:10.1000/x"
    assert node["source"] == "citation_reference"
    assert node["has_full_text"] is False


def test_build_co_citation_similarity_creates_bidirectional_edges() -> None:
    edges = [
        ("A", "R1"),
        ("A", "R2"),
        ("A", "R3"),
        ("B", "R1"),
        ("B", "R2"),
        ("B", "R4"),
    ]

    similarities = build_co_citation_similarity(edges, min_shared=2, min_score=0.5)

    assert len(similarities) == 2
    assert {s.source_id for s in similarities} == {"A", "B"}


def test_obsolescence_score_decreases_with_more_citations() -> None:
    old_low = compute_obsolescence_score(year=2010, citation_count=5, current_year=2026)
    old_high = compute_obsolescence_score(year=2010, citation_count=500, current_year=2026)

    assert old_low > old_high


def test_nightly_rebuild_returns_report_without_crashing(tmp_path) -> None:
    from scheduler.nightly_jobs import rebuild_phase2_graph
    from storage.metadata_db import MetadataDB

    db_path = tmp_path / "metadata.duckdb"
    db = MetadataDB(str(db_path))
    db.insert_paper({"source": "arxiv", "source_id": "1", "title": "Paper"})
    db.close()

    report = rebuild_phase2_graph(
        metadata_db_path=str(db_path),
        graph_db_path=str(tmp_path / "graph"),
    )

    assert hasattr(report, "papers_seen")


def test_merge_project_records_into_global_uses_deduplication() -> None:
    graph = FakeGraph()
    records = [
        {
            "source": "arxiv",
            "source_id": "1",
            "title": "Same",
            "doi": "10.1000/x",
            "version": 1,
        },
        {
            "source": "semantic_scholar",
            "source_id": "2",
            "title": "Same",
            "doi": "10.1000/x",
            "version": 3,
        },
    ]

    report = merge_project_records_into_global(
        graph,
        records,
        citation_edges=[("P1", "P2")],
        similarity_edges=[("P1", "P2", 0.7, "citation_overlap")],
    )

    assert report.project_records == 2
    assert report.unique_records == 1
    assert report.dedup_drops == 1
    assert report.citations_merged == 1
    assert report.similarities_merged == 1
