from __future__ import annotations

from pathlib import Path

import pytest

import scripts.try_phase1 as try_phase1


class FakeArxivClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def search(self, _query: str, max_results: int = 10):
        return [
            {
                "source": "arxiv",
                "source_id": "1111.1111",
                "title": "Paper A",
                "abstract": "A",
                "authors": ["Alice"],
                "year": 2024,
                "doi": "10.1000/demo",
                "pdf_url": "https://example.org/a.pdf",
                "landing_page_url": "https://arxiv.org/abs/1111.1111",
                "version": 1,
            }
            for _ in range(max_results)
        ]

    async def close(self) -> None:
        return None


class FakeSemanticScholarClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def search_papers(self, *_args, **_kwargs):
        return {
            "data": [
                {
                    "paperId": "s2-1",
                    "title": "Paper A",
                    "abstract": "A",
                    "authors": [{"name": "Alice"}],
                    "year": 2024,
                    "externalIds": {"DOI": "10.1000/demo"},
                    "openAccessPdf": {"url": "https://example.org/a.pdf"},
                    "url": "https://semanticscholar.org/paper/s2-1",
                }
            ]
        }

    async def close(self) -> None:
        return None


class FakeOpenAlexClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def list_works(self, *_args, **_kwargs):
        return {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "title": "Paper A",
                    "publication_year": 2024,
                    "ids": {"doi": "https://doi.org/10.1000/demo"},
                    "best_oa_location": {"pdf_url": "https://example.org/a.pdf"},
                    "authorships": [{"author": {"display_name": "Alice"}}],
                }
            ]
        }

    async def close(self) -> None:
        return None


class FakePapersWithCodeClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def search_papers(self, *_args, **_kwargs):
        return {"results": [{"id": "pwc-1"}]}

    async def close(self) -> None:
        return None


class FakeUnpaywallClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def best_oa_url(self, *_args, **_kwargs):
        return "https://example.org/a.pdf"

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_demo_full_phase1_works_with_mocked_clients(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(try_phase1, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
harvester:
  unpaywall:
    email: test@example.com
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(try_phase1, "ArxivClient", FakeArxivClient)
    monkeypatch.setattr(try_phase1, "SemanticScholarClient", FakeSemanticScholarClient)
    monkeypatch.setattr(try_phase1, "OpenAlexClient", FakeOpenAlexClient)
    monkeypatch.setattr(try_phase1, "PapersWithCodeClient", FakePapersWithCodeClient)
    monkeypatch.setattr(try_phase1, "UnpaywallClient", FakeUnpaywallClient)

    await try_phase1.run_demo("demo", max_results=3, download=False, full_phase1=True)

    db = try_phase1.MetadataDB(str(tmp_path / "data" / "metadata.duckdb"))
    # All mocked sources point to the same DOI/title, so dedup should reduce to a single record.
    assert db.count_papers() == 1
    dedup_rows = db.conn.execute("SELECT COUNT(*) FROM dedup_log").fetchone()[0]
    assert dedup_rows >= 1
    db.close()
