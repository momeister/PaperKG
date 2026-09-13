from __future__ import annotations

import pytest

from quality.study_quality import (
    StudyQuality,
    _aggregate_coi_status,
    _classify_industry_type,
    _design_to_evidence_level,
    _industry_funded,
    compute_study_quality,
    quality_flag_markers,
    rerank_by_quality,
)


def test_design_base_score_rct():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "RCT"}, "claims": []}
    )
    assert result.study_design == "rct"
    assert result.quality_score == pytest.approx(1.0, rel=1e-6)
    assert result.evidence_level == "high"


def test_design_base_score_meta_analysis():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "meta_analysis"}, "claims": []}
    )
    assert result.quality_score == pytest.approx(1.0)
    assert result.evidence_level == "high"


def test_design_base_score_observational():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "observational"}, "claims": []}
    )
    assert result.quality_score == pytest.approx(0.5)
    assert result.evidence_level == "low"


def test_design_base_score_case_report():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "case_report"}, "claims": []}
    )
    assert result.quality_score == pytest.approx(0.2)
    assert result.evidence_level == "very_low"


def test_unknown_design_falls_back():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "weird_design"}, "claims": []}
    )
    assert result.study_design == "unknown"
    assert result.quality_score == pytest.approx(0.5)
    assert result.evidence_level == "unknown"
    assert "sample_size_not_extracted" in result.flags


def test_small_sample_size_penalty():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "RCT"},
            "claims": [{"sample_size": {"value": 20, "unit": "participants"}}],
        }
    )
    assert result.sample_size_value == 20
    # RCT base 1.0 * 0.6 (n<30)
    assert result.quality_score == pytest.approx(0.6)
    # high base, but n<30 downgrades to low
    assert result.evidence_level == "low"


def test_very_small_sample_size_downgrades_to_very_low():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "cohort"},
            "claims": [{"sample_size": {"value": 5, "unit": "patients"}}],
        }
    )
    # cohort base 0.7 * 0.6 (n<30)
    assert result.quality_score == pytest.approx(0.42)
    assert result.evidence_level == "very_low"


def test_large_sample_boost():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "cohort"},
            "claims": [{"sample_size": {"value": 2000, "unit": "participants"}}],
        }
    )
    # cohort base 0.7 * 1.1 (n>1000)
    assert result.quality_score == pytest.approx(0.77)


def test_missing_sample_size_flagged():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "RCT"}, "claims": []}
    )
    assert "sample_size_not_extracted" in result.flags
    assert result.sample_size_value is None


def test_industry_funded_penalty():
    result = compute_study_quality(
        {
            "provenance": {
                "study_design_primary": "RCT",
                "funding_sources": [
                    {"name": "Pfizer", "role": "sponsor", "industry_type": "pharma"}
                ],
            },
            "claims": [],
        }
    )
    # RCT 1.0 * 0.8 (industry)
    assert result.quality_score == pytest.approx(0.8)
    assert any(flag.startswith("industry_funded:pharma") for flag in result.flags)


def test_industry_funded_inferred_from_name():
    is_ind, industry = _industry_funded([{"name": "Philip Morris"}])
    assert is_ind is True
    assert industry == "tobacco"


def test_non_industry_funding_not_flagged():
    result = compute_study_quality(
        {
            "provenance": {
                "study_design_primary": "RCT",
                "funding_sources": [{"name": "Max Planck Society"}],
            },
            "claims": [],
        }
    )
    assert not any(flag.startswith("industry_funded") for flag in result.flags)


def test_coi_undeclared_penalty():
    result = compute_study_quality(
        {
            "provenance": {
                "study_design_primary": "RCT",
                "conflicts_of_interest": [
                    {"author": "A", "declaration": "undisclosed"}
                ],
            },
            "claims": [],
        }
    )
    # RCT 1.0 * 0.9 (coi_undeclared)
    assert result.quality_score == pytest.approx(0.9)
    assert "coi_undeclared" in result.flags
    assert result.coi_status == "undisclosed"


def test_coi_declared_no_penalty():
    result = compute_study_quality(
        {
            "provenance": {
                "study_design_primary": "RCT",
                "conflicts_of_interest": [{"author": "A", "declaration": "declared"}],
            },
            "claims": [],
        }
    )
    assert result.quality_score == pytest.approx(1.0)
    assert "coi_undeclared" not in result.flags
    assert result.coi_status == "declared"


def test_coi_none_status():
    status = _aggregate_coi_status([{"declaration": "none"}, {"declaration": "none"}])
    assert status == "none"


def test_coi_unknown_when_no_declarations():
    status = _aggregate_coi_status([])
    assert status == "unknown"


def test_non_peer_reviewed_penalty():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "RCT"}, "claims": []},
        paper_meta={"peer_reviewed": False},
    )
    # RCT 1.0 * 0.85 (non_peer_reviewed)
    assert result.quality_score == pytest.approx(0.85)
    assert "non_peer_reviewed" in result.flags


def test_retracted_zero_score():
    result = compute_study_quality(
        {"provenance": {"study_design_primary": "RCT"}, "claims": []},
        paper_meta={"retracted": True},
    )
    assert result.quality_score == 0.0
    assert result.evidence_level == "very_low"
    assert "retracted" in result.flags


def test_retracted_overrides_everything():
    result = compute_study_quality(
        {
            "provenance": {
                "study_design_primary": "RCT",
                "funding_sources": [{"name": "Pfizer", "industry_type": "pharma"}],
                "conflicts_of_interest": [{"declaration": "undisclosed"}],
            },
            "claims": [{"sample_size": {"value": 5}}],
        },
        paper_meta={"retracted": True, "peer_reviewed": True},
    )
    # retracted short-circuits — other penalties don't apply
    assert result.quality_score == 0.0
    assert "retracted" in result.flags
    assert "industry_funded:pharma" not in result.flags
    assert "coi_undeclared" not in result.flags


def test_combined_penalties():
    result = compute_study_quality(
        {
            "provenance": {
                "study_design_primary": "observational",
                "funding_sources": [{"name": "Shell", "industry_type": "oil"}],
                "conflicts_of_interest": [{"declaration": "undisclosed"}],
            },
            "claims": [{"sample_size": {"value": 15}}],
        },
        paper_meta={"peer_reviewed": False},
    )
    # 0.5 (observational) * 0.6 (n<30) * 0.8 (industry) * 0.9 (coi) * 0.85 (non-pr)
    expected = 0.5 * 0.6 * 0.8 * 0.9 * 0.85
    assert result.quality_score == pytest.approx(expected, rel=1e-6)
    assert "industry_funded:oil" in result.flags
    assert "coi_undeclared" in result.flags
    assert "non_peer_reviewed" in result.flags


def test_evidence_level_from_result_overrides_inference():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "RCT"},
            "claims": [],
            "evidence_level": "moderate",
        }
    )
    # result_evidence_level moderate is valid → used directly
    assert result.evidence_level == "moderate"


def test_evidence_level_unknown_result_falls_back_to_inference():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "RCT"},
            "claims": [],
            "evidence_level": "unknown",
        }
    )
    assert result.evidence_level == "high"


def test_evidence_level_invalid_result_falls_back():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "RCT"},
            "claims": [],
            "evidence_level": "bogus",
        }
    )
    assert result.evidence_level == "high"


def test_design_to_evidence_level_high():
    assert _design_to_evidence_level("RCT", 500) == "high"


def test_design_to_evidence_level_downgrade_small():
    assert _design_to_evidence_level("RCT", 20) == "low"


def test_design_to_evidence_level_downgrade_tiny():
    assert _design_to_evidence_level("cohort", 5) == "very_low"


def test_design_to_evidence_level_unknown_design():
    assert _design_to_evidence_level("weird", 500) == "unknown"


def test_to_dict_roundtrip():
    sq = StudyQuality(
        study_design="rct",
        sample_size_value=200,
        evidence_level="high",
        quality_score=0.95,
        flags=["industry_funded:pharma"],
        funding_sources=[{"name": "Pfizer"}],
        coi_status="declared",
    )
    d = sq.to_dict()
    assert d["study_design"] == "rct"
    assert d["sample_size_value"] == 200
    assert d["evidence_level"] == "high"
    assert d["quality_score"] == pytest.approx(0.95)
    assert d["flags"] == ["industry_funded:pharma"]
    assert d["funding_sources"] == [{"name": "Pfizer"}]
    assert d["coi_status"] == "declared"


def test_compute_from_dataclass_like_object():
    class FakeResult:
        provenance = {"study_design_primary": "RCT"}
        claims = []
        evidence_level = "unknown"

    result = compute_study_quality(FakeResult())
    assert result.study_design == "rct"
    assert result.evidence_level == "high"


def test_compute_none_input():
    result = compute_study_quality(None)
    assert result.study_design == "unknown"
    assert result.quality_score == pytest.approx(0.5)
    assert "sample_size_not_extracted" in result.flags


def test_rerank_by_quality_mutates_score():
    hits = [
        {"paper_id": "A", "score": 1.0},
        {"paper_id": "B", "score": 0.8},
        {"paper_id": "C", "score": 0.5},
    ]
    quality_map = {
        "A": StudyQuality(quality_score=1.0),
        "B": StudyQuality(quality_score=0.0),
    }
    rerank_by_quality(hits, quality_map)
    # A: 1.0 * (0.5 + 0.5*1.0) = 1.0
    assert hits[0]["score"] == pytest.approx(1.0)
    # B: 0.8 * (0.5 + 0.5*0.0) = 0.4
    assert hits[1]["score"] == pytest.approx(0.4)
    # C: no quality entry → unchanged
    assert hits[2]["score"] == pytest.approx(0.5)


def test_rerank_by_quality_dict_map():
    hits = [{"paper_id": "X", "score": 1.0}]
    quality_map = {"X": {"quality_score": 0.6}}
    rerank_by_quality(hits, quality_map)
    # 1.0 * (0.5 + 0.5*0.6) = 0.8
    assert hits[0]["score"] == pytest.approx(0.8)


def test_rerank_by_quality_empty():
    assert rerank_by_quality([], {}) == []


def test_quality_flag_markers_retracted():
    sq = StudyQuality(flags=["retracted"])
    assert quality_flag_markers(sq) == "(⚠ Retracted)"


def test_quality_flag_markers_combined():
    sq = StudyQuality(
        flags=["industry_funded:pharma", "coi_undeclared", "non_peer_reviewed"]
    )
    markers = quality_flag_markers(sq)
    assert "Industry-funded (pharma)" in markers
    assert "COI undeclared" in markers
    assert "Preprint" in markers


def test_quality_flag_markers_empty():
    sq = StudyQuality(flags=[])
    assert quality_flag_markers(sq) == ""


def test_quality_flag_markers_none():
    assert quality_flag_markers(None) == ""


def test_quality_flag_markers_dict_input():
    d = {"flags": ["retracted"]}
    assert quality_flag_markers(d) == "(⚠ Retracted)"


def test_classify_industry_type():
    assert _classify_industry_type("Pfizer Inc.") == "pharma"
    assert _classify_industry_type("Philip Morris") == "tobacco"
    assert _classify_industry_type("Unknown Foundation") == "other"


def test_sample_size_picks_max_from_claims():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "cohort"},
            "claims": [
                {"sample_size": {"value": 100, "unit": "patients"}},
                {"sample_size": {"value": 500, "unit": "participants"}},
            ],
        }
    )
    assert result.sample_size_value == 500
    assert result.sample_size_unit == "participants"


def test_sample_size_ignored_when_zero():
    result = compute_study_quality(
        {
            "provenance": {"study_design_primary": "RCT"},
            "claims": [{"sample_size": {"value": 0, "unit": "participants"}}],
        }
    )
    assert result.sample_size_value is None
    assert "sample_size_not_extracted" in result.flags
