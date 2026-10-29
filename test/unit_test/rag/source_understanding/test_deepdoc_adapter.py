from __future__ import annotations

import hashlib

from deepdoc.parser.source_observation import (
    DeepDocBoundingBox,
    DeepDocExtractionSource,
    DeepDocObservation,
    DeepDocObservationKind,
    DeepDocParseResult,
    DeepDocSourceLocation,
)
from rag.source_understanding.adapters import (
    DeepDocPdfObservationBuilder,
    DeepDocSourceAdapter,
    validate_adapter,
)
from rag.source_understanding.schemas.context import StructureSource


class _PageImage:
    size = (600, 900)


class _PdfParserState:
    page_from = 0
    page_images = (_PageImage(), _PageImage())
    page_cum_height = (0.0, 300.0, 600.0)
    boxes = (
        {
            "text": "Second page paragraph",
            "page_number": 2,
            "x0": 20.0,
            "x1": 180.0,
            "top": 330.0,
            "bottom": 360.0,
            "layout_type": "text",
            "layoutno": "p2-text",
        },
        {
            "text": "Chapter One",
            "page_number": 1,
            "x0": 10.0,
            "x1": 190.0,
            "top": 20.0,
            "bottom": 50.0,
            "layout_type": "title",
            "layoutno": "p1-title",
        },
    )


class _PartialPdfParserState:
    page_from = 4
    page_images = (_PageImage(),)
    page_cum_height = (0.0, 300.0)
    boxes = (
        {
            "text": "Page five",
            "page_number": 5,
            "x0": 20.0,
            "x1": 180.0,
            "top": 30.0,
            "bottom": 60.0,
            "layout_type": "text",
        },
    )


def test_pdf_builder_interleaves_text_and_tables_by_page_geometry():
    parsed = DeepDocPdfObservationBuilder().build(
        _PdfParserState(),
        tables=(
            (
                (None, "<table><tr><td>A</td></tr></table>"),
                ((0, 10.0, 190.0, 100.0, 180.0),),
            ),
        ),
        zoomin=3,
    )

    assert [item.text for item in parsed.observations] == [
        "Chapter One",
        "<table><tr><td>A</td></tr></table>",
        "Second page paragraph",
    ]
    assert [item.order for item in parsed.observations] == [0, 1, 2]
    assert [item.type_hint for item in parsed.observations] == [
        "TITLE",
        "TABLE",
        "PARAGRAPH",
    ]
    assert parsed.observations[2].locations[0].bbox.y0 == 30


def test_pdf_builder_preserves_absolute_page_numbers_for_partial_parsing():
    parsed = DeepDocPdfObservationBuilder().build(
        _PartialPdfParserState(),
        tables=(
            (
                (None, [["A"]]),
                ((4, 10.0, 190.0, 100.0, 180.0),),
            ),
        ),
        zoomin=3,
    )

    assert [item.locations[0].page for item in parsed.observations] == [5, 5]


def test_adapter_hashes_exact_bytes_and_preserves_source_anchors():
    source = b"%PDF-test-source"
    parsed = DeepDocPdfObservationBuilder().build(
        _PdfParserState(),
        zoomin=3,
    )
    adapter = DeepDocSourceAdapter(parsed)

    assert validate_adapter(adapter)[0] == "deepdoc-observation-bridge"
    result = adapter.adapt(source, source_name="book.pdf")

    assert result.content_hash == f"sha256:{hashlib.sha256(source).hexdigest()}"
    assert result.metadata.source_name == "book.pdf"
    assert result.metadata.title == "book"
    assert result.raw_elements[0].type_hint == "TITLE"
    assert result.raw_elements[0].provenance.source == StructureSource.DERIVED
    assert result.raw_elements[0].location.page == 1
    assert result.raw_elements[0].location.bbox.x0 == 0.05
    assert result.raw_elements[0].attributes["source_anchor"]["kind"] == ("deepdoc_observation")


def test_adapter_does_not_clamp_out_of_page_bbox():
    parsed = DeepDocParseResult(
        parser_name="deepdoc-test",
        parser_version="1",
        media_type="application/pdf",
        observations=(
            DeepDocObservation(
                source_id="pdf:text:1:0",
                order=0,
                kind=DeepDocObservationKind.TEXT,
                text="Outside",
                extraction_source=DeepDocExtractionSource.NATIVE,
                locations=(
                    DeepDocSourceLocation(
                        page=1,
                        page_width=200,
                        page_height=300,
                        bbox=DeepDocBoundingBox(
                            x0=10,
                            y0=20,
                            x1=250,
                            y1=40,
                        ),
                    ),
                ),
            ),
        ),
    )

    result = DeepDocSourceAdapter(parsed).adapt(b"%PDF-source")

    assert result.raw_elements[0].location.page == 1
    assert result.raw_elements[0].location.bbox is None
    assert result.raw_elements[0].provenance.source == StructureSource.EXPLICIT
    assert [item.code for item in result.diagnostics] == ["DEEPDOC_BBOX_OUTSIDE_PAGE"]
