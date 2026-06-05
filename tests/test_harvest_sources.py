"""Unit tests for the new harvest-source normalizers (pure, no network)."""
from __future__ import annotations

from api.product_main import (
    _normalize_core_work,
    _normalize_crossref_work,
    _normalize_doaj_article,
    _normalize_europepmc_result,
)


def test_normalize_crossref_work_extracts_core_fields():
    work = {
        "DOI": "10.1000/abc",
        "title": ["A Study of Contract Law"],
        "abstract": "<jats:p>Hello <b>world</b></jats:p>",
        "author": [{"given": "Jane", "family": "Doe"}, {"family": "Smith"}],
        "issued": {"date-parts": [[2021, 5, 3]]},
        "link": [{"content-type": "application/pdf", "URL": "https://x/y.pdf"}],
        "URL": "https://doi.org/10.1000/abc",
    }
    out = _normalize_crossref_work(work)
    assert out["source"] == "crossref"
    assert out["title"] == "A Study of Contract Law"
    assert out["doi"] == "10.1000/abc"
    assert out["year"] == 2021
    assert out["authors"] == ["Jane Doe", "Smith"]
    assert out["pdf_url"] == "https://x/y.pdf"
    assert "<b>" not in out["abstract"]
    assert out["has_full_text"] is True


def test_normalize_europepmc_result_handles_open_access():
    item = {
        "id": "123",
        "doi": "10.1/med",
        "title": "Vaccine efficacy",
        "abstractText": "Results.",
        "authorString": "A, B, C",
        "pubYear": "2020",
        "isOpenAccess": "Y",
        "pmcid": "PMC999",
        "fullTextUrlList": {
            "fullTextUrl": [{"documentStyle": "pdf", "url": "https://epmc/full.pdf"}]
        },
    }
    out = _normalize_europepmc_result(item)
    assert out["source"] == "europepmc"
    assert out["pdf_url"] == "https://epmc/full.pdf"
    assert out["year"] == 2020
    assert out["authors"] == ["A", "B", "C"]
    assert out["has_full_text"] is True


def test_normalize_europepmc_result_source_override_for_biorxiv():
    out = _normalize_europepmc_result({"id": "1", "title": "Preprint"}, source="biorxiv")
    assert out["source"] == "biorxiv"


def test_normalize_core_work_uses_download_url():
    work = {
        "id": 42,
        "doi": "10.2/oa",
        "title": "Open paper",
        "abstract": "abs",
        "authors": [{"name": "X Y"}],
        "yearPublished": 2019,
        "downloadUrl": "https://core/dl.pdf",
    }
    out = _normalize_core_work(work)
    assert out["source"] == "core"
    assert out["pdf_url"] == "https://core/dl.pdf"
    assert out["year"] == 2019
    assert out["has_full_text"] is True


def test_normalize_doaj_article_extracts_fulltext_and_doi():
    article = {
        "id": "doaj1",
        "bibjson": {
            "title": "Economics of law",
            "abstract": "abs",
            "year": "2018",
            "author": [{"name": "L M"}],
            "identifier": [{"type": "doi", "id": "10.3/econ"}],
            "link": [{"type": "fulltext", "content_type": "PDF", "url": "https://doaj/x.pdf"}],
        },
    }
    out = _normalize_doaj_article(article)
    assert out["source"] == "doaj"
    assert out["doi"] == "10.3/econ"
    assert out["pdf_url"] == "https://doaj/x.pdf"
    assert out["year"] == 2018
