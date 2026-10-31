from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deepdoc.parser.pdf_glyph_inspector import PdfGlyphAnalysis, PdfGlyphInspection
from deepdoc.parser.pdf_glyph_repair import VerifiedFontGlyphCorrection
from deepdoc.parser.pdf_text_parser import PdfTextParser
from deepdoc.parser.source_observation import DeepDocExtractionSource

_XML = b"""<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>
<page width="600" height="900"><flow>
<block xMin="50" yMin="100" xMax="550" yMax="120"><line><word>Chapter</word><word>1</word></line></block>
<block xMin="50" yMin="150" xMax="550" yMax="200"><line><word>Native</word><word>text.</word></line></block>
</flow></page><page width="600" height="900"/></doc></body></html>"""

_AUTOMATIC_GLYPH_EVIDENCE = {
    "source_backend": "pypdf_content_stream_and_font_resources",
    "secondary_backend": "pypdfium2",
    "page": 1,
    "glyph_name": "/negationslash",
    "font_fingerprint": "font-fingerprint",
    "embedded_font_sha256": "a" * 64,
    "equals_character_code_glyph_name": "/equal",
    "equals_font_fingerprints": ["equal-font-fingerprint"],
    "equals_font_program_hashes": ["b" * 64],
    "glyph_bbox_normalized": [0.4, 0.4, 0.6, 0.6],
    "equals_bbox_normalized": [0.42, 0.48, 0.58, 0.52],
    "overlap_ratio": 0.75,
    "location_resolution": "character_bbox",
}


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
    empty_page_diagnostic = next(item for item in parsed.diagnostics if item.code == "PDF_PAGE_WITHOUT_NATIVE_TEXT")
    assert empty_page_diagnostic.metadata["page"] == 2
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


def test_repeated_running_headers_keep_source_text_but_do_not_become_headings():
    pages = "".join(f'<page width="600" height="900"><block xMin="50" yMin="70" xMax="550" yMax="90"><line><word>1.1. Topic {number}</word></line></block></page>' for number in range(1, 5))
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=f"<doc>{pages}</doc>".encode()),
            ],
        ),
    ):
        parsed = PdfTextParser().extract_source(b"%PDF-running-header")
    assert [item.text for item in parsed.observations[1:]] == ["1.1. Topic 2", "1.1. Topic 3", "1.1. Topic 4"]
    assert all(item.type_hint == "HEADER" and item.attributes["source_zone"] == "header" for item in parsed.observations[1:])


def test_verified_glyph_correction_preserves_original_and_evidence():
    source = b"%PDF-known-source"
    xml = _XML.replace(b"Native", b"6=")
    correction = {
        "pdf_sha256": sha256(source).hexdigest(),
        "page": 1,
        "block": 1,
        "original_text": "6= text.",
        "corrected_text": "≠ text.",
        "evidence": "visual inspection of page 1 at original resolution",
    }
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=xml),
            ],
        ),
    ):
        parsed = PdfTextParser().extract_source(source, glyph_corrections=[correction])

    corrected = parsed.observations[1]
    assert corrected.text == "≠ text."
    assert corrected.extraction_source == DeepDocExtractionSource.DERIVED
    assert corrected.attributes["glyph_provenance"]["original_text"] == "6= text."
    assert corrected.attributes["glyph_provenance"]["evidence"] == correction["evidence"]
    assert parsed.attributes["verified_glyph_correction_count"] == 1
    assert any(d.code == "PDF_VERIFIED_GLYPH_CORRECTION" for d in parsed.diagnostics)


def test_suspected_ascii_glyph_is_never_replaced_without_evidence():
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=_XML.replace(b"Native", b"6=")),
            ],
        ),
    ):
        parsed = PdfTextParser().extract_source(b"%PDF-unreviewed")
    assert parsed.observations[1].text == "6= text."
    assert "glyph_provenance" not in parsed.observations[1].attributes
    assert parsed.attributes["verified_glyph_correction_count"] == 0


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("pdf_sha256", "wrong-digest"),
        ("evidence", ""),
        ("page", 0),
        ("block", -1),
    ],
)
def test_verified_glyph_corrections_reject_invalid_scope(bad_field, bad_value):
    from deepdoc.parser.pdf_glyph_repair import prepare_verified_glyph_corrections

    source = b"test"
    correction = {
        "pdf_sha256": sha256(source).hexdigest(),
        "page": 1,
        "block": 0,
        "original_text": "6=",
        "corrected_text": "≠",
        "evidence": "verified visually",
    }
    correction[bad_field] = bad_value
    with pytest.raises(ValueError):
        prepare_verified_glyph_corrections(source, [correction])


def test_verified_glyph_correction_rejects_stale_poppler_text():
    from deepdoc.parser.pdf_glyph_repair import apply_verified_glyph_correction

    with pytest.raises(ValueError, match="must be reviewed again"):
        apply_verified_glyph_correction(
            "4=",
            page=1,
            block=0,
            corrections={(1, 0): {"original_text": "6=", "corrected_text": "≠", "evidence": "verified"}},
        )


def test_font_verified_recovery_is_integrated_and_preserves_source_observation_identity():
    source = b"%PDF-font-recovery"
    automatic = VerifiedFontGlyphCorrection(
        pdf_sha256=sha256(source).hexdigest(),
        page=1,
        block=1,
        original_text="6= text.",
        spans=((0, 2, "6=", "≠"),),
        font_fingerprints=("font-fingerprint", "equal-font-fingerprint"),
        evidence=(_AUTOMATIC_GLYPH_EVIDENCE,),
        method="font_glyph_name_negationslash_overlaid_equal",
    )
    inspection = PdfGlyphInspection(sha256(source).hexdigest(), 2, (), (), (1,), ())
    analysis = PdfGlyphAnalysis((), (automatic,))
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=_XML.replace(b"<word>Native</word><word>text.</word>", b"<word>6=</word><word>text.</word>")),
            ],
        ),
        patch("deepdoc.parser.pdf_text_parser.PdfGlyphInspector.inspect", return_value=inspection),
        patch("deepdoc.parser.pdf_text_parser.PdfiumTextBackend.extract_pages", return_value={1: ()}),
        patch("deepdoc.parser.pdf_text_parser.PdfGlyphInspector.verify", return_value=analysis),
    ):
        parsed = PdfTextParser().extract_source(source)

    recovered = parsed.observations[1]
    assert recovered.text == "≠ text."
    assert recovered.source_id == "pdf:text:1:1"
    assert recovered.order == 1
    assert recovered.attributes["glyph_provenance"]["original_text"] == "6= text."
    assert dict(recovered.attributes["glyph_provenance"]["spans"][0]) == {"start": 0, "end": 2, "original": "6=", "replacement": "≠"}
    assert parsed.attributes["font_glyph_recovery_count"] == 1
    assert parsed.attributes["pdf_glyph_inspection"]["status"] == "VERIFIED"


def test_manual_verified_glyph_correction_precedes_font_recovery_proposal():
    source = b"%PDF-manual-over-font"
    automatic = VerifiedFontGlyphCorrection(
        pdf_sha256=sha256(source).hexdigest(),
        page=1,
        block=1,
        original_text="6= text.",
        spans=((0, 2, "6=", "≠"),),
        font_fingerprints=("font-fingerprint", "equal-font-fingerprint"),
        evidence=(_AUTOMATIC_GLYPH_EVIDENCE,),
        method="font_glyph_name_negationslash_overlaid_equal",
    )
    inspection = PdfGlyphInspection(sha256(source).hexdigest(), 2, (), (), (1,), ())
    correction = {
        "pdf_sha256": sha256(source).hexdigest(),
        "page": 1,
        "block": 1,
        "original_text": "6= text.",
        "corrected_text": "MANUAL text.",
        "evidence": "reviewed by editor",
    }
    with (
        patch.dict("os.environ", {"PDFTOTEXT_EXECUTABLE": "/tools/pdftotext"}),
        patch(
            "deepdoc.parser.pdf_text_parser.subprocess.run",
            side_effect=[
                SimpleNamespace(stderr=b"pdftotext version 26.09.0", stdout=b""),
                SimpleNamespace(stderr=b"", stdout=_XML.replace(b"<word>Native</word><word>text.</word>", b"<word>6=</word><word>text.</word>")),
            ],
        ),
        patch("deepdoc.parser.pdf_text_parser.PdfGlyphInspector.inspect", return_value=inspection),
        patch("deepdoc.parser.pdf_text_parser.PdfiumTextBackend.extract_pages", return_value={1: ()}),
        patch("deepdoc.parser.pdf_text_parser.PdfGlyphInspector.verify", return_value=PdfGlyphAnalysis((), (automatic,))),
    ):
        parsed = PdfTextParser().extract_source(source, glyph_corrections=[correction])

    assert parsed.observations[1].text == "MANUAL text."
    assert parsed.observations[1].attributes["glyph_provenance"]["method"] == "exact_verified_block_override"
