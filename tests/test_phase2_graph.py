from __future__ import annotations

from graph.citation_analysis import (
    build_co_citation_similarity,
    compute_obsolescence_score,
)
from graph.paper_ingestion import (
    extract_citation_ids,
    ingest_records,
    to_phase2_paper_node,
)
from graph.project_global_merge import merge_project_records_into_global


class FakeGraph:
    def __init__(self) -> None:
        self.papers = {}
        self.citations = []
        self.similarities = []

    def merge_paper(self, paper: dict) -> None:
        self.papers[paper["id"]] = paper

    def merge_citation(self, from_paper_id: str, to_paper_id: str) -> None:
        self.citations.append((from_paper_id, to_paper_id))

    def merge_similarity(self, from_paper_id: str, to_paper_id: str, score: float, similarity_type: str) -> None:
        self.similarities.append((from_paper_id, to_paper_id, score, similarity_type))


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
