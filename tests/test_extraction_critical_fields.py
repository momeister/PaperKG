from __future__ import annotations

from extraction.entity_extractor._claims import ClaimsMixin


class _ExtractorStub(ClaimsMixin):
    """Standalone stub so we can call the mixin methods without instantiating
    the full EntityExtractor (which needs config/llm_router)."""

    @staticmethod
    def _normalize_label(text: str) -> str:
        import re

        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    @staticmethod
    def _text_before_references(text: str) -> str:
        # Minimal implementation: strip the references section if present.
        import re

        match = re.search(r"\bReferences\b", text, re.I)
        if match:
            return text[: match.start()].strip()
        return text.strip()


Stub = _ExtractorStub


def test_infer_study_design_rct():
    assert (
        Stub._infer_study_design("We conducted a randomized controlled trial.") == "rct"
    )


def test_infer_study_design_rct_acronym():
    assert Stub._infer_study_design("This RCT showed significant effects.") == "rct"


def test_infer_study_design_meta_analysis():
    assert Stub._infer_study_design("A meta-analysis of 12 studies.") == "meta_analysis"


def test_infer_study_design_cohort():
    assert (
        Stub._infer_study_design("A prospective cohort study of 500 patients.")
        == "cohort"
    )


def test_infer_study_design_case_control():
    assert Stub._infer_study_design("We used a case-control design.") == "case_control"


def test_infer_study_design_cross_sectional():
    assert (
        Stub._infer_study_design("A cross-sectional survey was conducted.")
        == "cross_sectional"
    )


def test_infer_study_design_observational():
    assert (
        Stub._infer_study_design("An observational study of behavior.")
        == "observational"
    )


def test_infer_study_design_case_report():
    assert (
        Stub._infer_study_design("We present a case report of a patient.")
        == "case_report"
    )


def test_infer_study_design_unknown():
    assert Stub._infer_study_design("We did some stuff.") == "unknown"


def test_infer_study_design_specific_before_generic():
    # "randomized controlled trial" should match RCT, not the generic "controlled" cue
    assert (
        Stub._infer_study_design("A double-blind placebo-controlled trial.")
        == "randomized_controlled"
    )


def test_infer_sample_size_n_equals():
    result = Stub._infer_sample_size("The study included n=42 participants.")
    assert result == {"value": 42, "unit": "participants"}


def test_infer_sample_size_capital_n():
    result = Stub._infer_sample_size("N = 1234 patients were enrolled.")
    assert result is not None
    assert result["value"] == 1234
    # The explicit n=/N= form always defaults unit to "participants".
    assert result["unit"] == "participants"


def test_infer_sample_size_count_unit():
    result = Stub._infer_sample_size("We recruited 200 subjects for the study.")
    assert result is not None
    assert result["value"] == 200
    assert result["unit"] == "subjects"


def test_infer_sample_size_individuals_normalized():
    result = Stub._infer_sample_size("Data from 150 individuals were collected.")
    assert result is not None
    assert result["value"] == 150
    assert result["unit"] == "participants"


def test_infer_sample_size_total_of():
    result = Stub._infer_sample_size("A total of 300 papers were reviewed.")
    assert result is not None
    assert result["value"] == 300
    assert result["unit"] == "papers"


def test_infer_sample_size_enrolled():
    result = Stub._infer_sample_size("We enrolled 88 patients over 2 years.")
    assert result is not None
    assert result["value"] == 88
    # "enrolled N" form defaults unit to participants, but the word "patients"
    # in the sentence doesn't override — only the count+unit pattern does.
    assert result["unit"] in {"participants", "patients"}


def test_infer_sample_size_none():
    assert Stub._infer_sample_size("No sample size mentioned.") is None


def test_infer_p_value_less_than():
    assert Stub._infer_p_value("The result was significant (p<0.05).") == "p<0.05"


def test_infer_p_value_equals():
    assert Stub._infer_p_value("p = 0.03 was reported.") == "p=0.03"


def test_infer_p_value_none():
    assert Stub._infer_p_value("No significance reported.") is None


def test_infer_confidence_interval_percent():
    result = Stub._infer_confidence_interval("95% CI 0.2-0.8")
    assert result is not None
    assert "95" in result


def test_infer_confidence_interval_bracket():
    result = Stub._infer_confidence_interval("confidence interval [0.2, 0.8]")
    assert result is not None


def test_infer_confidence_interval_none():
    assert Stub._infer_confidence_interval("No CI reported.") is None


def test_infer_effect_size_cohens_d():
    result = Stub._infer_effect_size("Cohen's d = 0.5")
    assert result is not None
    assert "Cohen" in result or "cohen" in result


def test_infer_effect_size_odds_ratio():
    result = Stub._infer_effect_size("odds ratio = 2.3")
    assert result is not None


def test_infer_effect_size_hazard_ratio():
    result = Stub._infer_effect_size("HR = 1.8")
    assert result is not None


def test_infer_effect_size_none():
    assert Stub._infer_effect_size("No effect size reported.") is None


def test_enrich_critical_fields_fills_all():
    claim = {
        "statement": "In this randomized controlled trial with n=100 participants, we found p<0.01.",
    }
    Stub._enrich_critical_fields(claim)
    assert claim["study_design"] == "rct"
    assert isinstance(claim["sample_size"], dict)
    assert claim["sample_size"]["value"] == 100
    assert claim["statistical_confidence"]["p_value"] == "p<0.01"
    assert "evidence_level" in claim


def test_enrich_critical_fields_preserves_existing():
    claim = {
        "statement": "A randomized controlled trial with n=50.",
        "study_design": "cohort",
        "sample_size": {"value": 200, "unit": "patients"},
        "evidence_level": "high",
    }
    Stub._enrich_critical_fields(claim)
    # Existing valid values must NOT be overwritten
    assert claim["study_design"] == "cohort"
    assert claim["sample_size"]["value"] == 200
    assert claim["evidence_level"] == "high"


def test_enrich_critical_fields_invalid_design_replaced():
    claim = {"statement": "A randomized controlled trial.", "study_design": "bogus"}
    Stub._enrich_critical_fields(claim)
    assert claim["study_design"] == "rct"


def test_enrich_evidence_level_rct_large():
    claim = {"statement": "RCT with n=500 participants."}
    Stub._enrich_critical_fields(claim)
    assert claim["study_design"] == "rct"
    assert claim["evidence_level"] == "high"


def test_enrich_evidence_level_rct_small():
    claim = {"statement": "RCT with n=20 participants."}
    Stub._enrich_critical_fields(claim)
    assert claim["evidence_level"] == "low"


def test_enrich_evidence_level_rct_tiny():
    claim = {"statement": "RCT with n=5 participants."}
    Stub._enrich_critical_fields(claim)
    assert claim["evidence_level"] == "very_low"


def test_enrich_evidence_level_unknown_design():
    claim = {"statement": "We did some stuff with n=100."}
    Stub._enrich_critical_fields(claim)
    assert claim["study_design"] == "unknown"
    assert claim["evidence_level"] == "unknown"


def test_enrich_significance_reported():
    claim = {"statement": "The difference was statistically significant (p<0.05)."}
    Stub._enrich_critical_fields(claim)
    assert claim["statistical_confidence"]["significance_reported"] is True


def test_enrich_significance_not_reported():
    claim = {"statement": "We observed a trend in the data."}
    Stub._enrich_critical_fields(claim)
    assert claim["statistical_confidence"]["significance_reported"] is False


def test_merge_claim_lists_enriches():
    claims = [
        {"statement": "In our RCT with n=100, we found p<0.05."},
        {"statement": "The cohort study showed no effect."},
    ]
    merged = Stub._merge_claim_lists(claims)
    assert len(merged) == 2
    assert merged[0]["study_design"] == "rct"
    assert merged[0]["sample_size"]["value"] == 100
    assert merged[0]["statistical_confidence"]["p_value"] == "p<0.05"
    assert merged[1]["study_design"] == "cohort"


def test_merge_claim_lists_dedup():
    claims = [
        {"statement": "We found a significant effect."},
        {"statement": "We found a significant effect."},
    ]
    merged = Stub._merge_claim_lists(claims)
    assert len(merged) == 1


def test_merge_claim_lists_sets_defaults():
    claims = [{"statement": "A claim without fields."}]
    merged = Stub._merge_claim_lists(claims)
    assert merged[0]["evidence_type"] == "theoretical"
    assert merged[0]["attributed_to"] == "this_paper"
    assert "claim_type" in merged[0]
    assert "negated" in merged[0]
    assert "study_design" in merged[0]
    assert "evidence_level" in merged[0]


def test_fallback_claims_from_text_enriched():
    text = """
    Abstract
    In this randomized controlled trial with n=250 participants, we found that
    the treatment was effective (p<0.01). Our results show a significant improvement.

    Introduction
    Some background text here.
    """
    claims = Stub._fallback_claims_from_text(text, limit=5)
    assert len(claims) >= 1
    # At least one claim should have critical fields enriched
    has_enriched = any(
        c.get("study_design") == "rct"
        or c.get("statistical_confidence", {}).get("p_value")
        for c in claims
    )
    assert has_enriched
