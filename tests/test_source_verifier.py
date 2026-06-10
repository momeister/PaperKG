from __future__ import annotations

from query.source_verifier import (
    best_excerpt,
    best_excerpts,
    find_pdf_path,
    locate_evidence_fragments,
    reference_fragments,
    reference_text,
    verbatim_excerpt,
    verify_answer_sources,
)


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
                "evidence_id": "ev-clinical-16",
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
    assert payload["sources"][0]["evidence"][0]["evidence_id"] == "ev-clinical-16"
    assert payload["sources"][0]["evidence"][0]["source_evidence_index"] == 0
    assert payload["sources"][0]["evidence"][0]["fragment_index"] == 0


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


def test_paper_reference_fragments_prefer_precise_evidence_span_over_abstract() -> None:
    fragments = reference_fragments(
        {
            "paper_id": "p1",
            "kind": "paper",
            "text": "broad fallback text",
            "metadata": {
                "title": "Clinical AI Study",
                "abstract": (
                    "This broad abstract discusses implementation, diagnostics, alert fatigue, "
                    "deployment, safety, clinicians, and many other themes."
                ),
                "evidence_span": "Clinicians with access to AI Consult made 16% fewer diagnostic errors.",
            },
        },
        max_fragments=2,
    )

    assert fragments == ["Clinicians with access to AI Consult made 16% fewer diagnostic errors."]
    assert "broad abstract" not in fragments[0].lower()


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


def test_best_excerpt_returns_precise_sentence_not_broad_shared_term_window() -> None:
    pdf_text = (
        "Implementation notes mention clinical AI deployment, clinicians, diagnostics, "
        "safety, and alert fatigue across a broad introductory paragraph. "
        "Clinicians with access to AI Consult made 16% fewer diagnostic errors. "
        "A separate limitations paragraph discusses workflow integration."
    )
    reference = "Clinicians with access to AI Consult made 16% fewer diagnostic errors."

    excerpt = best_excerpt(pdf_text, reference, window_chars=100)

    assert excerpt == reference
    assert "broad introductory" not in excerpt


def test_best_excerpt_keeps_quantitative_claim_tokens_when_matching_pdf_text() -> None:
    pdf_text = (
        "These results demonstrate potential for LLM-based clinical decision support tools "
        "to reduce errors in real-world settings and provide a framework for responsible adoption. "
        "Clinicians with access to AI Consult made 16% fewer diagnostic errors and 13% fewer treatment errors. "
        "Additional workflow discussion follows."
    )
    reference = "AI Consult reduced diagnostic errors by 16% and treatment errors by 13% in primary care settings."

    excerpt = best_excerpt(pdf_text, reference, window_chars=120)

    assert "16% fewer diagnostic errors" in excerpt
    assert "13% fewer treatment errors" in excerpt
    assert "demonstrate potential" not in excerpt


def test_best_excerpt_matches_german_decimal_comma_numbers_against_english_pdf_text() -> None:
    # German claim text writes decimals with a comma ("10,6"); the PDF (English, NEJM-style)
    # writes them with a period ("10.6"). Without normalizing the comma to a period first,
    # _quantitative_tokens splits "10,6" into separate integer tokens {10, 6}, the anchor
    # tier never finds "10.6"/"6.2" in the PDF, and best_excerpt falls back to an unrelated
    # number-dense table instead of the actual claim sentence.
    pdf_text = (
        "Baseline characteristics were balanced between groups across all measured covariates. "
        "The median progression-free survival was 10.6 months in the bevacizumab group as compared "
        "with 6.2 months in the placebo group, a difference that was statistically significant. "
        "Quality of life scores were similar between groups at twelve months of follow-up."
    )
    reference = (
        "Die Studie zeigte ein progressionsfreies Überleben von 10,6 Monaten in der "
        "Bevacizumab-Gruppe gegenüber 6,2 Monaten in der Placebo-Gruppe."
    )

    excerpt = best_excerpt(pdf_text, reference, window_chars=160)

    assert "10.6 months" in excerpt
    assert "6.2 months" in excerpt
    assert "Baseline characteristics" not in excerpt


def test_best_excerpt_strict_mode_rejects_weak_generic_overlap_but_keeps_concrete_anchors() -> None:
    # `strict=True` is for callers that anchor a SPECIFIC claim's citation (the
    # "PDF-Assistent" claim-evidence path): a window that only shares ubiquitous,
    # recurring terms (drug/disease names appearing throughout the whole paper) with
    # the reference has no concrete anchor and is more likely to mislead than help —
    # better to report "no match" (the caller then falls back to a generic, honestly
    # labeled snippet) than a confident-looking excerpt that doesn't actually support
    # the claim. Exact-phrase and number-anchored matches must still work normally.
    pdf_text = (
        "Bevacizumab plus Radiotherapy-Temozolomide for Glioblastoma. Jane Doe, MD, John Smith, PhD. "
        "We report the results of a phase 3 trial of bevacizumab plus radiotherapy-temozolomide as "
        "compared with placebo plus radiotherapy-temozolomide in patients with newly diagnosed glioblastoma. "
        "Quality of life: No clinically meaningful differences in baseline quality of life and performance "
        "status were observed with bevacizumab as compared with placebo during the study period. "
        "The median overall survival was 16.8 months in the bevacizumab group as compared with 12.3 months "
        "in the placebo group, a difference that was statistically significant."
    )

    # Only "bevacizumab"/"glioblastoma" overlap with the PDF — both occur in the title,
    # methods, AND results sections, so there is no single concrete window they anchor to.
    weak_reference = "Die Behandlung mit Bevacizumab führte bei Glioblastom-Patienten zu einer Veränderung der Lebensqualität."
    assert best_excerpt(pdf_text, weak_reference, strict=True) == ""
    # Non-strict mode keeps its existing, more permissive behavior (some excerpt is returned).
    assert best_excerpt(pdf_text, weak_reference, strict=False) != ""

    exact_phrase_reference = "No clinically meaningful differences in baseline quality of life and performance status were observed with bevacizumab"
    strict_excerpt = best_excerpt(pdf_text, exact_phrase_reference, window_chars=160, strict=True)
    assert "baseline quality of life" in strict_excerpt

    number_anchor_reference = "Median overall survival reached 16.8 months with bevacizumab versus 12.3 months with placebo."
    strict_number_excerpt = best_excerpt(pdf_text, number_anchor_reference, window_chars=160, strict=True)
    assert "16.8 months" in strict_number_excerpt
    assert "12.3 months" in strict_number_excerpt


def test_verify_claim_excerpt_evidence_passes_through_located_excerpt() -> None:
    # The answer pipeline already located the precise (English) PDF passage for a
    # (German) cited sentence. Verification must display that excerpt verbatim instead
    # of re-locating from the paraphrased sentence — which used to land on decoys.
    pdf_text = (
        "Decoy: quality of life scores were similar between groups throughout the study period. "
        "Glucocorticoid use over the course of the study was lower among patients who received "
        "bevacizumab than among those who received placebo. Further filler text follows here."
    )
    evidence = {
        "evidence_id": "ev-claim-1",
        "paper_id": "files",
        "kind": "pdf",
        "field": "answer_claim_excerpt",
        "text": (
            "Glucocorticoid use over the course of the study was lower among patients who received "
            "bevacizumab than among those who received placebo."
        ),
        "metadata": {
            "context": "Zudem war der Bedarf an Glukokortikoiden in der Bevacizumab-Gruppe geringer",
            "context_policy": "claim_excerpt",
            "title": "Bevacizumab Trial",
        },
    }

    locations = locate_evidence_fragments(evidence, pdf_text, max_fragments=3, source_evidence_index=0)

    assert len(locations) == 1
    location = locations[0]
    assert location.pdf_excerpt == evidence["text"]
    assert location.reference_text.startswith("Zudem war der Bedarf")
    assert location.found_in_pdf_text is True
    assert "quality of life" not in location.pdf_excerpt
    assert location.source_evidence_index == 0
    assert location.fragment_index == 0


def test_verify_claim_excerpt_without_pdf_text_marks_unverified() -> None:
    evidence = {
        "paper_id": "files",
        "kind": "pdf",
        "field": "answer_claim_excerpt",
        "text": "A located excerpt that cannot be checked without parsed PDF text right now.",
        "metadata": {"context": "Der zitierte Satz", "context_policy": "claim_excerpt"},
    }

    location = locate_evidence_fragments(evidence, "")[0]

    assert location.found_in_pdf_text is False
    assert location.pdf_excerpt == evidence["text"]
    assert location.reference_text == "Der zitierte Satz"


def test_best_excerpts_merges_adjacent_clause_matches_into_one_longer_excerpt() -> None:
    pdf_text = (
        "Introduction text comes first in this manuscript. "
        "Median overall survival reached 16.8 months in the treatment group during follow-up. "
        "Patients in the treatment group also reported more fatigue and headaches than placebo. "
        "Unrelated discussion of study limitations and future work closes the section."
    )
    reference = (
        "Median overall survival reached 16.8 months in the treatment group; "
        "patients reported more fatigue and headaches than placebo"
    )

    excerpts = best_excerpts(pdf_text, reference, max_excerpts=3, strict=True)

    assert len(excerpts) == 1
    assert "16.8 months" in excerpts[0]
    assert "fatigue and headaches" in excerpts[0]


def test_best_excerpts_keeps_scattered_facts_as_separate_excerpts() -> None:
    filler = "Entirely unrelated methodological discussion continues here at considerable length. " * 8
    pdf_text = (
        "Median overall survival reached 16.8 months in the treatment group during follow-up. "
        + filler
        + "Patients in the treatment group reported more fatigue and headaches than placebo overall."
    )
    reference = (
        "Median overall survival reached 16.8 months in the treatment group; "
        "patients reported more fatigue and headaches than placebo"
    )

    excerpts = best_excerpts(pdf_text, reference, max_excerpts=3, strict=True)

    assert len(excerpts) == 2
    assert any("16.8 months" in excerpt for excerpt in excerpts)
    assert any("fatigue and headaches" in excerpt for excerpt in excerpts)


def test_best_excerpt_expands_tiny_exact_matches_with_surrounding_context() -> None:
    pdf_text = (
        "Study design follows the registered protocol for all participating centres. "
        "Prospectively collected and analysed. "
        "Outcomes were assessed by blinded reviewers at twelve months of follow-up."
    )
    reference = "Prospectively collected and analysed."

    excerpt = best_excerpt(pdf_text, reference)

    assert "prospectively collected and analysed" in excerpt.lower()
    # The bare snippet alone carries no context - it must be expanded to its neighbours.
    assert len(excerpt) > len(reference) + 20


def test_locate_evidence_falls_back_to_larger_approx_region_when_strict_fails() -> None:
    pdf_text = (
        "Background section of the manuscript describes prior work in depth. "
        "Participants completed memory recall training and made fewer cognitive errors at "
        "follow-up compared with the control group in this trial. "
        "The appendix lists additional secondary outcomes and exploratory analyses."
    )
    evidence = {
        "paper_id": "p1",
        "kind": "claim",
        "text": "The study reported 45% fewer cognitive errors with memory recall training.",
        "metadata": {},
    }

    location = locate_evidence_fragments(evidence, pdf_text, max_fragments=1)[0]

    # 45% appears nowhere in the PDF -> no confident anchor, but the topic region exists:
    # show the larger approximate region and flag it for the UI.
    assert location.found_in_pdf_text is True
    assert "memory recall training" in location.pdf_excerpt
    assert location.metadata.get("located") == "approx_region"


def test_reference_fragments_keep_decimal_numbers_intact() -> None:
    fragments = reference_fragments(
        {
            "paper_id": "p1",
            "kind": "claim",
            "metadata": {
                "statement": (
                    "The hazard ratio was 0.65 with a 95% confidence interval of 0.55 to 0.78 "
                    "favouring the treatment arm in the primary analysis of the trial."
                )
            },
        },
        max_fragments=3,
    )

    assert any("0.55 to 0.78" in fragment for fragment in fragments)
    # The old sentence scanner split decimals apart, producing fragments like "55 to 0".
    assert all(not fragment.startswith("55 ") for fragment in fragments)


def test_best_excerpt_never_truncates_the_match_away() -> None:
    # One very long sentence without inner sentence boundaries; the matching number sits
    # near its end. Plain head-truncation used to cut the match off ("..., 0.55..." bug).
    pdf_text = (
        "The investigators enrolled a large multicentre cohort of adult participants with "
        "the condition of interest and followed them for a long observation period across "
        "many sites with careful central adjudication of all endpoint events before the "
        "final statistical analysis showed an overall response rate of 42.7% in the group"
    )
    reference = "overall response rate was 42.7% in the group"

    excerpt = best_excerpt(pdf_text, reference, strict=True)

    assert "42.7" in excerpt


def test_verbatim_excerpt_locates_quote_whitespace_insensitively() -> None:
    pdf_text = (
        "Background discussion appears first. Median overall survival was 16.8 months in "
        "the treatment group as compared with 11.2 months in the placebo group. Further "
        "secondary outcomes are described later in the manuscript."
    )
    quote = "Median  overall survival was 16.8 months\nin the treatment group"

    excerpt = verbatim_excerpt(pdf_text, quote)

    assert "16.8 months" in excerpt
    assert "11.2 months" in excerpt  # expanded to the full sentence
    assert verbatim_excerpt(pdf_text, "this quote does not occur in the text at all") == ""


def test_verify_answer_sources_keeps_fragmenting_non_claim_evidence() -> None:
    # KG concept evidence also carries a metadata "context" key; without the
    # claim_excerpt context_policy it must keep the legacy fragment path.
    evidence = {
        "paper_id": "p1",
        "kind": "concept",
        "field": "concepts",
        "text": "Graph Transformer",
        "metadata": {
            "context": (
                "The graph transformer is the central architecture of the system. "
                "It links concepts across scientific papers."
            )
        },
    }

    locations = locate_evidence_fragments(evidence, "", max_fragments=3)

    assert len(locations) >= 2
    assert all(not location.pdf_excerpt for location in locations)
