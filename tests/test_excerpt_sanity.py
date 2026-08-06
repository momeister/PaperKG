from __future__ import annotations

from query.excerpt_sanity import filter_sane_excerpts, is_garbled_excerpt


def test_reversed_pdf_column_excerpt_is_flagged() -> None:
    # Verbatim from the user-reported bevacizumab leak (DB turn
    # turn_1785960597585_99bc6dd21db838): "detanoitcarfopyH ii esahP yG 6*6
    # TR citcatoerets ... AN 11 AN 7 3 36 72 04". Contains reversed
    # "detanoitcarfopyH" (Hypofractionated), "esahP" (Phase), "orumO iC" (Ci
    # Oumo), "AN" (NA), "yG" (Gy).
    garbled = (
        "S ecnerefeR DP DS RP RC )%( )%( )%( )%( AN 11 AN 7 3 36 72 04 yG 6*6 "
        "( TR citcatoerets detanoitcarfopyH ii esahP orumO iC %59 yG 4*6"
    )
    assert is_garbled_excerpt(garbled)


def test_normal_prose_passes() -> None:
    prose = (
        "Hypofractionated stereotactic radiotherapy was administered in 3 phases. "
        "Patients received a total dose of 36 Gy in 6 fractions."
    )
    assert not is_garbled_excerpt(prose)


def test_terse_abstract_with_abbreviations_passes() -> None:
    # Real abstract style with units and acronyms — must NOT be flagged.
    text = (
        "Bevacizumab combined with radiotherapy improved OS in glioblastoma. "
        "Median survival increased from 16 to 21 months (p < 0.05). "
        "Adverse events included hypertension and fatigue."
    )
    assert not is_garbled_excerpt(text)


def test_short_text_is_not_flagged() -> None:
    assert not is_garbled_excerpt("Gy NA")
    assert not is_garbled_excerpt("")


def test_filter_sane_excerpts_drops_garbled_entries() -> None:
    good = "Patients received 36 Gy in 6 fractions of radiotherapy."
    garbled = (
        "AN 11 AN 7 3 36 72 04 yG 6*6 detanoitcarfopyH esahP orumO iC citcatoerets"
    )
    filtered = filter_sane_excerpts([garbled, good, garbled])
    assert filtered == [good]


def test_filter_sane_excerpts_keeps_least_bad_when_all_garbled() -> None:
    # When every entry is garbled, the longest is kept (more recoverable context)
    # rather than returning an empty evidence list.
    short_garbled = "AN 11 esahP detanoitcarfopyH citcatoerets"
    long_garbled = (
        "AN 11 AN 7 3 36 72 04 yG 6*6 detanoitcarfopyH esahP orumO iC citcatoerets "
        "detanoitcarfopyH esahP yG orumO iC detanoitcarfopyH esahP yG orumO iC"
    )
    filtered = filter_sane_excerpts([short_garbled, long_garbled])
    assert filtered == [long_garbled]


def test_filter_sane_excerpts_empty_input() -> None:
    assert filter_sane_excerpts([]) == []
