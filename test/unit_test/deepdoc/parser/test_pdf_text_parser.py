from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deepdoc.parser.pdf_text_parser import PdfTextParser
from deepdoc.parser.source_observation import DeepDocExtractionSource


_XML = b"""<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>
<page width="600" height="900"><flow>
<block xMin="50" yMin="100" xMax="550" yMax="120"><line><word>Chapter</word><word>1</word></line></block>
<block xMin="50" yMin="150" xMax="550" yMax="200"><line><word>Native</word><word>text.</word></line></block>
</flow></page><page width="600" height="900"/></doc></body></html>"""


def test_native_parser_preserves_text_geometry_and_diagnoses_empty_pages_without_ocr():
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=_XML),
            ],
        ) as run,
    ):
        parsed = PdfTextParser().extract_source(b"%PDF-fixture")

    assert parsed.parser_name == "poppler-pdftotext"
    assert parsed.attributes["ocr_used"] is False
    assert parsed.attributes["page_count"] == 2
    assert parsed.attributes["text_page_count"] == 1
    assert parsed.observations[0].type_hint == "HEADING"
    assert parsed.observations[0].extraction_source == DeepDocExtractionSource.DERIVED
    assert parsed.observations[1].text == "Native text."
    assert parsed.observations[1].extraction_source == DeepDocExtractionSource.NATIVE
    assert parsed.observations[1].locations[0].page == 1
    assert parsed.observations[1].locations[0].bbox.x0 == 50
    assert parsed.diagnostics[0].metadata["page"] == 2
    extraction = run.call_args_list[1]
    assert extraction.args[0] == ["/tools/pdftotext", "-bbox-layout", "-enc", "UTF-8", "-", "-"]
    assert extraction.kwargs["input"] == b"%PDF-fixture"


def test_no_native_text_fails_without_ocr_fallback():
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=b'<html xmlns="http://www.w3.org/1999/xhtml"><page width="600" height="900"/></html>'),
            ],
        ),
        pytest.raises(ValueError, match="never falls back to OCR"),
    ):
        PdfTextParser().extract_source(b"%PDF-image-only")


def test_pdf_control_characters_remain_visible_to_quality_checks():
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=_XML.replace(b"Native", b"Na\x01tive")),
            ],
        ),
    ):
        parsed = PdfTextParser().extract_source(b"%PDF-control-character")
    assert parsed.observations[1].text == "Na\x01tive text."
