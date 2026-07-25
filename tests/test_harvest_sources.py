"""Unit tests for the harvest-source registry and normalizers (pure, no network)."""
from __future__ import annotations

import httpx
import pytest

from api.product_main import (
    _normalize_core_work,
    _normalize_crossref_work,
    _normalize_doaj_article,
    _normalize_europepmc_result,
)
from api.routers.harvest import (
    _external_paper_url,
    _normalize_dblp_publication,
    _normalize_doab_book,
    _normalize_eric_record,
    _normalize_hal_document,
    _normalize_openaire_product,
    _normalize_openalex_work,
    _openalex_abstract,
    _run_harvest_search,
    harvest_sources,
)
from harvester.source_registry import DEFAULT_SOURCES, HARVEST_GROUPS, HARVEST_SOURCES, source_tier


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


# --- Registry <-> Dispatch ---------------------------------------------------


def test_catalog_lists_every_source_in_an_existing_group():
    catalog = harvest_sources()
    assert catalog["default"] == DEFAULT_SOURCES
    group_ids = {group["id"] for group in HARVEST_GROUPS}
    assert {group["id"] for group in catalog["groups"]} == group_ids
    for source in HARVEST_SOURCES:
        assert source["group"] in group_ids, source["id"]
        assert source["tier"] in {"journal", "preprint", "index"}
    assert set(DEFAULT_SOURCES) <= {source["id"] for source in HARVEST_SOURCES}


@pytest.fixture
def offline_httpx(monkeypatch):
    """Jeder ausgehende Request scheitert sofort — kein Netz im Unit-Test.

    Alle Harvest-Clients laufen ueber ``httpx.AsyncClient`` (direkt oder ueber
    ``harvester/http_client.py``), ein Riegel reicht also fuer alle Quellen.
    Ohne ihn ruft der Dispatch-Test wirklich jede registrierte Quelle auf und
    kann den gesamten Suite-Lauf blockieren: ``ArxivClient`` allein wiederholt
    bis zu 6x mit 10-60s Backoff. ``ConnectError`` (nicht ``TimeoutException``)
    umgeht genau diese Retry-Schleife.
    """

    async def _refuse(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise httpx.ConnectError("offline im Test")

    monkeypatch.setattr(httpx.AsyncClient, "send", _refuse)


async def test_every_registered_source_has_a_dispatch_branch(offline_httpx):
    """Keine Quelle aus der Registry faellt in den else-Zweig.

    Geprueft wird nur, dass keine ID unbekannt ist. Die Clients scheitern durch
    ``offline_httpx`` sofort am Netz — das erscheint als andere Warnung und ist
    hier egal.
    """
    ids = [source["id"] for source in HARVEST_SOURCES]
    _results, warnings = await _run_harvest_search("", ids, 1)
    assert [w for w in warnings if "unbekannte Quelle" in w] == []
    # Jede Quelle muss den Dispatch ueberhaupt erreicht haben, sonst wuerde der
    # Test auch bei einer stillschweigend uebersprungenen ID gruen sein.
    assert len(warnings) == len(ids)


async def test_unknown_source_is_reported(offline_httpx):
    _results, warnings = await _run_harvest_search("x", ["definitely_not_a_source"], 1)
    assert warnings == ["definitely_not_a_source: unbekannte Quelle"]


def test_source_tier_ranks_journals_before_preprints():
    assert source_tier("europepmc") == "journal"
    assert source_tier("arxiv") == "preprint"
    assert source_tier("nicht-registriert") == "index"


# --- Metadaten-Rettung fuer Paper ohne PDF -----------------------------------


def test_openalex_abstract_is_rebuilt_from_inverted_index():
    work = {
        "id": "https://openalex.org/W1",
        "title": "T",
        "abstract_inverted_index": {"Machine": [0], "learning": [1], "works": [2, 4], "well": [3]},
        "authorships": [],
    }
    assert _openalex_abstract(work) == "Machine learning works well works"
    # Ohne die Rueckwandlung waere der Abstract leer — und damit weder Vorschau
    # noch Abstract-only-Extraktion moeglich.
    assert _normalize_openalex_work(work)["abstract"].startswith("Machine learning")


def test_openalex_abstract_handles_missing_or_direct_field():
    assert _openalex_abstract({"id": "x"}) == ""
    assert _openalex_abstract({"abstract": "direkt"}) == "direkt"


def test_external_paper_url_prefers_landing_then_doi_then_pdf():
    assert _external_paper_url("https://example.org/p", "10.1/2", None) == "https://example.org/p"
    assert _external_paper_url(None, "10.1/2", None) == "https://doi.org/10.1/2"
    assert _external_paper_url(None, "https://doi.org/10.1/2", None) == "https://doi.org/10.1/2"
    assert _external_paper_url(None, None, "https://cdn.example/a.pdf") == "https://cdn.example/a.pdf"
    # Ein lokaler Pfad ist kein Link zum Original.
    assert _external_paper_url(None, None, "data/pdfs/x.pdf") is None
    assert _external_paper_url(None, None, None) is None


# --- Normalizer der neuen Quellen -------------------------------------------


def test_normalize_dblp_publication_keeps_doi_link():
    out = _normalize_dblp_publication({
        "key": "conf/x/Y23",
        "title": "Knowledge Graphs.",
        "authors": {"author": [{"text": "A B"}, {"text": "C D"}]},
        "year": "2023",
        "doi": "10.5/kg",
        "ee": "https://doi.org/10.5/kg",
    })
    assert out["source"] == "dblp"
    assert out["title"] == "Knowledge Graphs"
    assert out["authors"] == ["A B", "C D"]
    assert out["landing_page_url"] == "https://doi.org/10.5/kg"
    assert out["abstract"] == ""  # DBLP fuehrt keine Abstracts


def test_normalize_dblp_publication_accepts_single_author_object():
    out = _normalize_dblp_publication({"key": "k", "title": "T", "authors": {"author": {"text": "Solo"}}})
    assert out["authors"] == ["Solo"]


def test_normalize_openaire_product_reads_doi_and_description():
    out = _normalize_openaire_product({
        "id": "oa::1",
        "mainTitle": "EU Study",
        "descriptions": ["Zusammenfassung"],
        "publicationDate": "2022-03-01",
        "pids": [{"scheme": "doi", "value": "10.7/eu"}],
        "authors": [{"fullName": "E F"}],
        "instances": [{"urls": ["https://repo/eu.pdf"]}],
    })
    assert out["source"] == "openaire"
    assert out["year"] == 2022
    assert out["doi"] == "10.7/eu"
    assert out["abstract"] == "Zusammenfassung"
    assert out["pdf_url"] == "https://repo/eu.pdf"


def test_normalize_eric_record_builds_fulltext_and_landing_url():
    out = _normalize_eric_record({
        "id": "EJ123",
        "title": "Reading",
        "description": "abs",
        "author": ["G H"],
        "publicationdateyear": 2021,
        "e_fulltextauth": True,
    })
    assert out["pdf_url"] == "https://files.eric.ed.gov/fulltext/EJ123.pdf"
    assert out["landing_page_url"] == "https://eric.ed.gov/?id=EJ123"
    assert out["has_full_text"] is True


def test_normalize_eric_record_without_fulltext_keeps_landing_url():
    out = _normalize_eric_record({"id": "ED9", "title": "T"})
    assert out["pdf_url"] is None
    assert out["landing_page_url"] == "https://eric.ed.gov/?id=ED9"


def test_normalize_doab_book_reads_dublin_core():
    out = _normalize_doab_book({
        "uuid": "u1",
        "name": "Fallback",
        "handle": "20.500/1",
        "metadata": [
            {"key": "dc.title", "value": "Copyright"},
            {"key": "dc.contributor.author", "value": "I J"},
            {"key": "dc.description.abstract", "value": "abs"},
            {"key": "dc.date.issued", "value": "2020-01-01"},
            {"key": "dc.identifier.uri", "value": "https://doabooks/handle/20.500/1"},
        ],
    })
    assert out["source"] == "doab"
    assert out["title"] == "Copyright"
    assert out["authors"] == ["I J"]
    assert out["year"] == 2020
    assert out["landing_page_url"] == "https://doabooks/handle/20.500/1"


def test_normalize_hal_document_unwraps_solr_lists():
    out = _normalize_hal_document({
        "docid": "42",
        "title_s": ["Droit d'auteur"],
        "abstract_s": ["résumé"],
        "authFullName_s": ["K L"],
        "producedDateY_i": 2019,
        "doiId_s": "10.9/hal",
        "uri_s": "https://hal.science/hal-42",
        "fileMain_s": "https://hal.science/hal-42/document",
    })
    assert out["source"] == "hal"
    assert out["title"] == "Droit d'auteur"
    assert out["abstract"] == "résumé"
    assert out["year"] == 2019
    assert out["has_full_text"] is True
