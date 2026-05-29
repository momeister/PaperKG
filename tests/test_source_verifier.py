from __future__ import annotations

from query.source_verifier import best_excerpt, find_pdf_path, reference_text, verify_answer_sources


def test_find_pdf_path_uses_paper_id_and_title_tokens(tmp_path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    expected = pdf_dir / "arxiv_ai-based-clinical-decision-support_2507.16947_v1.pdf"
    expected.write_bytes(b"%PDF-1.4\n")

    found = find_pdf_path(
        "arxiv:2507.16947",
        "AI-based Clinical Decision Support for Primary Care",
        str(pdf_dir),
    )

    assert found == str(expected)


def test_verify_answer_sources_maps_sources_evidence_and_citations(tmp_path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "2507.16947.pdf").write_bytes(b"%PDF-1.4\n")
    answer = {
        "answer": "AI Consult reduced diagnostic errors [arxiv:2507.16947]. Missing source [arxiv:0000.00000].",
        "sources": [
            {
                "paper_id": "arxiv:2507.16947",
                "title": "AI-based Clinical Decision Support for Primary Care",
            }
        ],
        "evidence": [
            {
                "paper_id": "arxiv:2507.16947",
                "kind": "claim",
                "field": "claims",
                "text": "Clinicians with AI Consult made 16% fewer diagnostic errors.",
                "metadata": {"statement": "Clinicians with AI Consult made 16% fewer diagnostic errors."},
            }
        ],
    }

    report = verify_answer_sources(answer, pdf_base_dir=str(pdf_dir), parse_pdfs=False)
    payload = report.to_dict()

    assert payload["cited_paper_ids"] == ["arxiv:0000.00000", "arxiv:2507.16947"]
    assert payload["missing_source_ids"] == ["arxiv:0000.00000"]
    assert payload["sources"][0]["pdf_available"] is True
    assert payload["sources"][0]["evidence"][0]["reference_text"].startswith("Clinicians with AI Consult")


def test_best_excerpt_finds_nearest_matching_pdf_text() -> None:
    pdf_text = (
        "Background text. "
        "The clinical decision support tool AI Consult reduced diagnostic errors in primary care. "
        "More discussion follows."
    )
    reference = "AI Consult reduced diagnostic errors"

    excerpt = best_excerpt(pdf_text, reference, window_chars=80)

    assert "AI Consult reduced diagnostic errors" in excerpt


def test_reference_text_preserves_paper_metadata_anchors() -> None:
    text = reference_text(
        {
            "text": "AI Consult safety net",
            "metadata": {
                "title": "AI-based Clinical Decision Support for Primary Care",
                "authors": ["Robert Korom", "Sarah Kiptinness"],
                "abstract": "We evaluate the impact of AI Consult in live primary care.",
            },
        }
    )

    assert text.startswith("AI-based Clinical Decision Support")
    assert "Robert Korom" in text
    assert "We evaluate the impact" in text
