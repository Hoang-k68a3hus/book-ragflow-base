from __future__ import annotations

from rag.source_understanding.profiling.lexical_quality import (
    LexicalAnomalyReason,
    LexicalLanguage,
    LexicalQualityScorer,
)
from rag.source_understanding.schemas.context import StructureSource
from rag.source_understanding.schemas.element import Element, ElementType, Provenance


def _element(text: str) -> Element:
    return Element(
        id="element-1",
        order=0,
        type=ElementType.PARAGRAPH,
        raw_text=text,
        normalized_text=text,
        provenance=Provenance(source=StructureSource.EXPLICIT, extractor="test"),
    )


def test_vietnamese_scorer_finds_high_confidence_orthographic_anomalies():
    report = LexicalQualityScorer().score(
        (_element("Nguyễn Húu Dư và PHƯÌNG TRÌNH"),),
        language_hint="vi",
    )

    assert report.detected_language == LexicalLanguage.VIETNAMESE
    assert report.anomaly_count == 2
    assert report.evidence_truncated is False
    assert report.reason_counts == {
        "INVALID_VIETNAMESE_VOWEL_NUCLEUS": 1,
        "MISPLACED_VIETNAMESE_TONE": 1,
    }
    assert {(item.token, item.reason) for item in report.evidence} == {
        ("Húu", LexicalAnomalyReason.INVALID_VIETNAMESE_VOWEL_NUCLEUS),
        ("PHƯÌNG", LexicalAnomalyReason.MISPLACED_VIETNAMESE_TONE),
    }


def test_normal_vietnamese_and_english_are_not_marked_as_anomalies():
    scorer = LexicalQualityScorer()

    vi = scorer.score((_element("Nguyễn Hữu Dư và PHƯƠNG TRÌNH"),), language_hint="Vietnamese")
    en = scorer.score(
        (_element("This is an English document with normal words and structure."),),
        language_hint="en-US",
    )

    assert vi.anomaly_count == 0
    assert en.anomaly_count == 0


def test_english_hint_reports_non_english_diacritics_without_rewriting_text():
    element = _element("The cafe text was extracted as café.")
    report = LexicalQualityScorer().score((element,), language_hint="English")

    assert report.detected_language == LexicalLanguage.ENGLISH
    assert report.evidence[0].token == "café"
    assert report.evidence[0].reason == LexicalAnomalyReason.ENGLISH_UNEXPECTED_DIACRITIC
    assert element.text == "The cafe text was extracted as café."
