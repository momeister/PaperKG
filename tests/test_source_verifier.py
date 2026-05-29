from __future__ import annotations

from query.source_verifier import best_excerpt, find_pdf_path, reference_fragments, reference_text, verify_answer_sources


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
    assert len(excerpt) <= 120


def test_reference_text_prefers_precise_evidence_over_paper_metadata() -> None:
    text = reference_text(
        {
            "text": "AI Consult safety net",
            "metadata": {
                "title": "AI-based Clinical Decision Support for Primary Care",
                "authors": ["Robert Korom", "Sarah Kiptinness"],
                "abstract": "We evaluate the impact of AI Consult in live primary care.",
                "statement": "Clinicians with AI Consult made 16% fewer diagnostic errors.",
            },
        }
    )

    assert text == "Clinicians with AI Consult made 16% fewer diagnostic errors."
    assert "Robert Korom" not in text
    assert "We evaluate the impact" not in text


def test_reference_fragments_split_long_evidence_into_short_sentence_anchors() -> None:
    fragments = reference_fragments(
        {
            "paper_id": "arxiv:2507.16947",
            "kind": "claim",
            "metadata": {
                "evidence_span": (
                    "AI Consult integrates into clinical workflows and activates only when needed. "
                    "Clinicians with access to AI Consult made 16% fewer diagnostic errors. "
                    "The deployment used a low-friction interface to avoid broad disruption and alert fatigue."
                )
            },
        },
        max_fragments=3,
    )

    assert len(fragments) == 3
    assert all(len(fragment) <= 220 for fragment in fragments)
    assert any("16% fewer diagnostic errors" in fragment for fragment in fragments)


def test_verify_answer_sources_returns_multiple_short_locations_per_evidence() -> None:
    answer = {
        "answer": "AI Consult changed clinical workflows [arxiv:2507.16947].",
        "sources": [{"paper_id": "arxiv:2507.16947", "title": "AI-based Clinical Decision Support"}],
        "evidence": [
            {
                "paper_id": "arxiv:2507.16947",
                "kind": "claim",
                "text": "fallback text",
                "metadata": {
                    "evidence_span": (
                        "AI Consult integrates into clinical workflows and activates only when needed. "
                        "Clinicians with access to AI Consult made 16% fewer diagnostic errors."
                    )
                },
            }
        ],
    }

    report = verify_answer_sources(answer, parse_pdfs=False, max_evidence_per_source=4)
    evidence = report.to_dict()["sources"][0]["evidence"]

    assert len(evidence) == 2
    assert all(len(item["reference_text"]) <= 220 for item in evidence)


def test_reference_fragments_do_not_bundle_two_short_sentences_into_one_quote() -> None:
    fragments = reference_fragments(
        {
            "paper_id": "p1",
            "kind": "claim",
            "metadata": {
                "statement": "First conclusion comes from this sentence. Second conclusion is separate evidence."
            },
        },
        max_fragments=3,
    )

    assert fragments == [
        "First conclusion comes from this sentence.",
        "Second conclusion is separate evidence.",
    ]


def test_paper_reference_fragments_drop_title_prefix_before_abstract() -> None:
    fragments = reference_fragments(
        {
            "paper_id": "p1",
            "kind": "paper",
            "text": (
                "Grounding Clinical AI Competency in Human Cognition Through the Clinical World Model "
                "Theoretical work emphasizes that clinical AI lacks a formal account of the world."
            ),
            "metadata": {
                "title": "Grounding Clinical AI Competency in Human Cognition Through the Clinical World Model",
                "abstract": "Theoretical work emphasizes that clinical AI lacks a formal account of the world.",
            },
        },
        max_fragments=2,
    )

    assert fragments == ["Theoretical work emphasizes that clinical AI lacks a formal account of the world."]
    assert "Grounding Clinical AI Competency" not in fragments[0]


def test_best_excerpt_stays_sentence_near_match_without_cross_page_title_context() -> None:
    pdf_text = (
        "Grounding Clinical AI Competency in Human Cognition Through the Clinical World Model "
        "Seyed Amir Ahmadi Safavi-Naini. BREAK--- 47 Supplementary Information. "
        "Theoretical work emphasizes that clinical AI lacks a formal account of the world. "
        "A second abstract sentence follows."
    )
    reference = "Theoretical work emphasizes that clinical AI lacks a formal account of the world."

    excerpt = best_excerpt(pdf_text, reference, window_chars=120)

    assert excerpt == reference
    assert "Grounding Clinical AI" not in excerpt
