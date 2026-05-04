from harvester.deduplication import deduplicate_papers


def test_deduplicate_by_doi_keeps_highest_version() -> None:
    records = [
        {
            "source": "arxiv",
            "source_id": "1234.5678",
            "title": "A test paper",
            "doi": "10.1000/test",
            "version": 1,
        },
        {
            "source": "arxiv",
            "source_id": "1234.5678",
            "title": "A test paper",
            "doi": "https://doi.org/10.1000/test",
            "version": 3,
        },
    ]

    unique, decisions = deduplicate_papers(records)

    assert len(unique) == 1
    assert unique[0]["version"] == 3
    assert len(decisions) == 1
    assert decisions[0].reason == "same_doi"


def test_deduplicate_by_normalized_title() -> None:
    records = [
        {
            "source": "openalex",
            "source_id": "A",
            "title": "Neural   Networks: A Survey",
            "doi": None,
            "version": 1,
        },
        {
            "source": "semantic_scholar",
            "source_id": "B",
            "title": "neural networks a survey",
            "doi": None,
            "version": 1,
        },
    ]

    unique, decisions = deduplicate_papers(records)

    assert len(unique) == 1
    assert len(decisions) == 1
    assert decisions[0].reason == "same_title"
