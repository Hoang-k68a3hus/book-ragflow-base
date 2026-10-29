from __future__ import annotations

from deepdoc.parser.source_observation import (
    DeepDocExtractionSource,
    DeepDocObservation,
    DeepDocObservationKind,
    DeepDocParseResult,
    DeepDocSourceLocation,
)
from rag.source_understanding.adapters import DeepDocSourceAdapter, SourceAdapterRunner


def _observation(
    order: int,
    text: str,
    type_hint: str,
    page: int,
    source: DeepDocExtractionSource = DeepDocExtractionSource.NATIVE,
) -> DeepDocObservation:
    return DeepDocObservation(
        source_id=f"pdf:text:{page}:{order}",
        order=order,
        kind=DeepDocObservationKind.TEXT,
        text=text,
        extraction_source=source,
        type_hint=type_hint,
        locations=(
            DeepDocSourceLocation(
                page=page,
                page_width=600,
                page_height=900,
            ),
        ),
    )


def test_non_content_headings_do_not_pollute_context_tree():
    parsed = DeepDocParseResult(
        parser_name="test",
        parser_version="1",
        media_type="application/pdf",
        observations=(
            _observation(0, "NHÀ XUẤT BẢN GIÁO DỤC", "HEADING", 1),
            _observation(1, "MỤC LỤC", "HEADING", 2),
            _observation(2, "Chương một ........ 12", "HEADING", 2),
            _observation(3, "CAGR Compound annual growth rate: tốc độ tăng trưởng kép", "HEADING", 3),
            _observation(4, "CHƯƠNG 1. GIỚI THIỆU", "HEADING", 6),
            _observation(5, "Nội dung chương.", "PARAGRAPH", 6),
        ),
    )

    result = (
        SourceAdapterRunner()
        .understand_bytes(
            b"%PDF-test",
            adapter=DeepDocSourceAdapter(parsed),
            document_id="book-quality-test",
        )
        .understanding
    )

    assert [node.label for node in result.document.context_nodes] == ["CHƯƠNG 1. GIỚI THIỆU"]
    assert set(result.hierarchy_result.suppression_reasons.values()) == {
        "FRONT_MATTER_METADATA",
        "GLOSSARY_DEFINITION",
        "NAVIGATION_OR_TOC",
    }
    diagnostics = result.document.processing.configuration["source_understanding_pipeline"]["hierarchy_suppression_diagnostics"]
    assert diagnostics["suppressed_count"] == 4
    assert diagnostics["reason_counts"] == {
        "FRONT_MATTER_METADATA": 1,
        "GLOSSARY_DEFINITION": 1,
        "NAVIGATION_OR_TOC": 2,
    }
    final_context = result.integration_result.logical_units[-1].context_node_ids
    assert final_context == (result.document.context_nodes[0].id,)


def test_untrusted_inferred_heading_boundary_is_suppressed_instead_of_failing():
    parsed = DeepDocParseResult(
        parser_name="test",
        parser_version="1",
        media_type="application/pdf",
        observations=(
            _observation(0, "Chapter title", "HEADING", 1, DeepDocExtractionSource.DERIVED),
            _observation(1, "Possible subtitle", "HEADING", 1, DeepDocExtractionSource.DERIVED),
            _observation(2, "Body text follows.", "PARAGRAPH", 1, DeepDocExtractionSource.DERIVED),
        ),
    )

    result = (
        SourceAdapterRunner()
        .understand_bytes(
            b"%PDF-inferred-heading-boundary",
            adapter=DeepDocSourceAdapter(parsed),
            document_id="book-inferred-heading-boundary-test",
        )
        .understanding
    )

    assert [node.label for node in result.document.context_nodes] == ["Chapter title"]
    suppressed = result.hierarchy_result.suppression_reasons
    assert len(suppressed) == 1
    assert next(iter(suppressed.values())) == "INFERRED_STRUCTURAL_WITHOUT_CONFIDENT_BOUNDARY"


def test_text_quality_contributes_to_overall_quality():
    parsed = DeepDocParseResult(
        parser_name="test",
        parser_version="1",
        media_type="application/pdf",
        observations=(
            _observation(0, "PHƯƠNG TRÌNH VÀ NGÀNH C ẢNG BI ỂN Ãƒ", "HEADING", 1),
            _observation(1, "Nội dung bình thường.", "PARAGRAPH", 1),
        ),
    )

    result = (
        SourceAdapterRunner()
        .understand_bytes(
            b"%PDF-text-quality",
            adapter=DeepDocSourceAdapter(parsed),
            document_id="book-text-quality-test",
        )
        .understanding
    )

    quality = result.document.quality
    assert quality.text_quality is not None and quality.text_quality < 1.0
    assert quality.structure_quality is not None
    assert quality.overall is not None and quality.overall < quality.structure_quality
    assert result.text_quality_report.metrics.suspicious_spacing_count > 0
    assert result.text_quality_report.metrics.mojibake_marker_count > 0
    assert quality.lexical is not None
    assert quality.lexical.anomaly_count == result.text_quality_report.lexical_quality.anomaly_count
    assert "text_lexical_anomaly_evidence" not in quality.metrics
    assert quality.metrics["overall_quality_method"] == "harmonic_mean_available_dimensions"
    assert quality.order_quality is not None
