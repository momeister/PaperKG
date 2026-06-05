"""Tests for reference-section extraction and splitting (pure, no network)."""
from __future__ import annotations

from extraction.reference_parser import extract_reference_section, split_reference_entries


PAPER = """
# Introduction

This work studies attention mechanisms in detail. We build on prior work.

## Methods

We use transformers.

## References

[1] A. Vaswani et al. Attention is all you need. NeurIPS, 2017.
[2] J. Devlin et al. BERT: Pre-training of deep bidirectional transformers. NAACL, 2019.
[3] T. Brown et al. Language models are few-shot learners. NeurIPS, 2020.

## Appendix

Extra material that is not a reference at all.
"""


def test_extract_reference_section_returns_only_bibliography():
    section = extract_reference_section(PAPER)
    assert "Attention is all you need" in section
    assert "Extra material" not in section
    assert "Introduction" not in section


def test_split_reference_entries_finds_three_numbered_refs():
    section = extract_reference_section(PAPER)
    entries = split_reference_entries(section)
    assert len(entries) == 3
    assert any("BERT" in entry for entry in entries)
    assert all("2017" in entries[0] or "2019" in entries[1] or "2020" in entries[2] for _ in [0])


def test_no_reference_section_returns_empty():
    assert extract_reference_section("Just a body with no bibliography.") == ""
    assert split_reference_entries("") == []


def test_blank_line_separated_references_without_markers():
    section = (
        "Smith J. A theory of law. Harvard Law Review, 2015.\n\n"
        "Doe A. Economics of contracts. Journal of Economics, 2018.\n\n"
        "Roe B. Privacy regulation. Law Journal, 2020."
    )
    entries = split_reference_entries(section)
    assert len(entries) == 3
