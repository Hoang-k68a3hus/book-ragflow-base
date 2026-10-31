from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from deepdoc.parser.pdf_glyph_backend import PdfGlyphPageGeometry, PdfiumCharacter, _to_display_bbox
from deepdoc.parser.pdf_glyph_inspector import (
    GlyphTextBlock,
    PdfGlyphInspector,
    _backend_character_matches,
    _parse_tounicode,
    detect_text_anomalies,
    glyph_name_to_unicode,
    normalized_font_name,
)


def _font(writer, base_name, differences, *, font_program: bytes | None):
    encoding = DictionaryObject(
        {
            NameObject("/BaseEncoding"): NameObject("/WinAnsiEncoding"),
            NameObject("/Differences"): ArrayObject([value if isinstance(value, NumberObject) else NameObject(value) for value in differences]),
        }
    )
    descriptor = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/FontDescriptor"),
            NameObject("/FontName"): NameObject(base_name),
            NameObject("/Flags"): NumberObject(4),
            NameObject("/FontBBox"): ArrayObject([NumberObject(value) for value in (0, 0, 1000, 1000)]),
        }
    )
    if font_program is not None:
        stream = DecodedStreamObject()
        stream.set_data(font_program)
        descriptor[NameObject("/FontFile")] = writer._add_object(stream)
    descriptor_ref = writer._add_object(descriptor)
    return writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject(base_name),
                NameObject("/Encoding"): encoding,
                NameObject("/FontDescriptor"): descriptor_ref,
            }
        )
    )


def _pdf(*, repetitions=1, source_overlay=True, plain_suffix=b"", conflicting_equal_font=False, embedded=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    source_name = "/ABCDEF+LMMathSymbols10-Regular"
    equal_name = "/UVWXYZ+LMRoman10-Regular"
    source_glyph = "/negationslash" if source_overlay else "/six"
    source_font = _font(
        writer,
        source_name,
        [NumberObject(54), source_glyph],
        font_program=b"source-font-program" if embedded else None,
    )
    equal_font = _font(
        writer,
        equal_name,
        [NumberObject(61), "/equal"],
        font_program=b"equal-font-program" if embedded else None,
    )
    fonts = {NameObject("/Fsrc"): source_font, NameObject("/Feq"): equal_font}
    if conflicting_equal_font:
        fonts[NameObject("/Feq2")] = _font(
            writer,
            equal_name,
            [NumberObject(62), "/equal"],
            font_program=b"different-equal-font-program",
        )
    fonts[NameObject("/Fplain")] = _font(
        writer,
        "/PLAIN+PlainFont",
        [NumberObject(48), "/zero", NumberObject(49), "/one", NumberObject(54), "/six", NumberObject(61), "/equal"],
        font_program=b"plain-font-program",
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject(fonts)})
    extra = b" /Fplain 12 Tf ( and 6=) Tj" if plain_suffix else b""
    source_text = b"6" * repetitions
    equal_text = b"=" * repetitions
    content = b"BT /Fsrc 12 Tf (" + source_text + b") Tj /Feq 12 Tf (" + equal_text + b") Tj" + extra + b" ET"
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _cid_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    stream = DecodedStreamObject()
    stream.set_data(b"cid-font-program")
    stream_ref = writer._add_object(stream)
    descriptor = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/FontDescriptor"),
            NameObject("/FontName"): NameObject("/CIDTest"),
            NameObject("/FontFile2"): stream_ref,
        }
    )
    descendant = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType2"),
            NameObject("/BaseFont"): NameObject("/CIDTest"),
            NameObject("/FontDescriptor"): writer._add_object(descriptor),
            NameObject("/CIDToGIDMap"): NameObject("/Identity"),
        }
    )
    descendant_ref = writer._add_object(descendant)
    cmap = DecodedStreamObject()
    cmap.set_data(
        b"""1 begincodespacerange <0000> <ffff> endcodespacerange
        1 beginbfchar <0036> <0036> endbfchar"""
    )
    outer = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/CIDTest"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject([descendant_ref]),
            NameObject("/ToUnicode"): writer._add_object(cmap),
        }
    )
    outer_ref = writer._add_object(outer)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/FCID"): outer_ref})})
    content = DecodedStreamObject()
    content.set_data(b"BT /FCID 12 Tf <0036> Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _block(text: str, *, line_box=(0.0, 0.0, 100.0, 100.0)):
    return GlyphTextBlock(
        page=1,
        block=0,
        source_id="pdf:text:1:0",
        text=text,
        page_width=100.0,
        page_height=100.0,
        lines=(text,),
        line_boxes=(line_box,),
        bbox=(0.0, 0.0, 100.0, 100.0),
    )


def _backend_chars(repetitions=1):
    math_positions = (0.24, 0.74)
    return tuple(
        [PdfiumCharacter(1, index, "6", (x, 0.45, x + 0.04, 0.55), "LMMathSymbols10-Regular", "normalized_display_frame") for index, x in enumerate(math_positions[:repetitions])]
        + [PdfiumCharacter(1, repetitions + index, "=", (x - 0.01, 0.49, x + 0.05, 0.51), "LMRoman10-Regular", "normalized_display_frame") for index, x in enumerate(math_positions[:repetitions])]
    )


def test_real_font_glyph_evidence_recovers_only_the_exact_overlaid_occurrence():
    data = _pdf()
    inspector = PdfGlyphInspector()
    inspection = inspector.inspect(data, pages_to_inspect={1})
    assert inspection.pages_to_verify == (1,)
    use = next(item for item in inspection.code_uses if item.glyph_name == "/negationslash")
    source_font = next(item for item in inspection.font_resources if item.resource_name == "/Fsrc")
    assert use.character_code == 54
    assert use.glyph_id is None and use.glyph_id_status == "unavailable_without_cid"
    assert source_font.embedded_font_sha256
    assert source_font.to_unicode_status == "missing"
    assert source_font.normalized_base_font == "LMMathSymbols10-Regular"

    analysis = inspector.verify(inspection, {1: _backend_chars()}, [_block("6=")])
    assert [finding["status"] for finding in analysis.findings] == ["VERIFIED"]
    assert len(analysis.corrections) == 1
    correction = analysis.corrections[0]
    assert correction.spans == ((0, 2, "6=", "≠"),)
    assert correction.method == "font_glyph_name_negationslash_overlaid_equal"
    assert correction.evidence[0]["location_resolution"] == "character_bbox"


def test_repeated_overlay_glyphs_align_one_to_one_by_geometry_and_order():
    data = _pdf(repetitions=2)
    inspector = PdfGlyphInspector()
    inspection = inspector.inspect(data, pages_to_inspect={1})
    analysis = inspector.verify(inspection, {1: _backend_chars(2)}, [_block("6= + 6=")])
    assert len(analysis.corrections) == 1
    assert analysis.corrections[0].spans == ((0, 2, "6=", "≠"), (5, 7, "6=", "≠"))


def test_literal_6_equals_and_mixed_literal_occurrence_are_never_broadly_replaced():
    normal_pdf = _pdf(source_overlay=False)
    normal_inspector = PdfGlyphInspector()
    normal_inspection = normal_inspector.inspect(normal_pdf, pages_to_inspect={1})
    assert normal_inspection.pages_to_verify == ()
    assert not normal_inspector.verify(normal_inspection, {}, [_block("6=")]).corrections

    mixed_pdf = _pdf(plain_suffix=b" and 6=")
    inspector = PdfGlyphInspector()
    inspection = inspector.inspect(mixed_pdf, pages_to_inspect={1})
    mixed_block = _block("6= and 6=")
    result = inspector.verify(inspection, {1: _backend_chars()}, [mixed_block])
    assert result.corrections == ()
    assert any(finding["status"] == "CONFLICT" for finding in result.findings)


def test_ambiguous_same_name_equal_fonts_and_missing_secondary_backend_fail_closed():
    inspector = PdfGlyphInspector()
    ambiguous = inspector.inspect(_pdf(conflicting_equal_font=True), pages_to_inspect={1})
    result = inspector.verify(ambiguous, {1: _backend_chars()}, [_block("6=")])
    assert result.corrections == ()
    assert any(finding["status"] == "CONFLICT" for finding in result.findings)

    inspection = inspector.inspect(_pdf(), pages_to_inspect={1})
    unavailable = inspector.verify(inspection, {}, [_block("6=")])
    assert unavailable.corrections == ()
    assert any(finding["code"] == "PDF_GLYPH_SECONDARY_BACKEND_UNAVAILABLE" for finding in unavailable.findings)


def test_type0_character_code_cid_and_gid_are_reported_as_separate_values():
    inspection = PdfGlyphInspector().inspect(_cid_pdf(), pages_to_inspect={1}, suspicious_pages={1})
    use = inspection.code_uses[0]
    assert use.character_code == 0x36
    assert use.cid == 0x36 and use.cid_status == "identity_encoding_cmap"
    assert use.glyph_id == 0x36 and use.glyph_id_status == "identity_cid_to_gid_map"
    font = inspection.font_resources[0]
    assert font.to_unicode_entry_count == 1
    assert font.to_unicode_status == "available"


def test_different_documents_with_same_unembedded_font_name_have_document_scoped_fingerprints():
    first = PdfGlyphInspector().inspect(_pdf(embedded=False), pages_to_inspect={1})
    second = PdfGlyphInspector().inspect(_pdf(embedded=False, plain_suffix=b" additional"), pages_to_inspect={1})
    first_font = next(item for item in first.font_resources if item.resource_name == "/Fsrc")
    second_font = next(item for item in second.font_resources if item.resource_name == "/Fsrc")
    assert first_font.base_font == second_font.base_font
    assert first_font.embedded_font_sha256 is None and second_font.embedded_font_sha256 is None
    assert first_font.font_fingerprint != second_font.font_fingerprint


def test_text_anomaly_detection_does_not_flag_valid_math_or_ascii_6_equals():
    findings = detect_text_anomalies([_block("6= ∈ ≠ ∑ α"), _block("a\ufffd\ue000\x01")])
    assert [finding["code"] for finding in findings] == [
        "PDF_TEXT_REPLACEMENT_CHARACTER",
        "PDF_TEXT_PRIVATE_USE_CHARACTER",
        "PDF_TEXT_CONTROL_CHARACTER",
    ]
    assert findings[0]["secondary_verification_recommended"] is True
    assert findings[1]["secondary_verification_recommended"] is False
    cid = detect_text_anomalies([_block("unmapped CID+123")])
    assert [finding["code"] for finding in cid] == ["PDF_TEXT_CID_PLACEHOLDER"]
    assert cid[0]["secondary_verification_recommended"] is True


def test_coordinate_alignment_failure_and_stale_font_repair_fail_closed():
    inspector = PdfGlyphInspector()
    inspection = inspector.inspect(_pdf(), pages_to_inspect={1})
    result = inspector.verify(
        inspection,
        {1: _backend_chars()},
        [_block("6=", line_box=(0.0, 80.0, 100.0, 100.0))],
    )
    assert result.corrections == ()
    assert any(finding["status"] == "SUSPECT" for finding in result.findings)

    from deepdoc.parser.pdf_glyph_repair import apply_verified_font_glyph_correction

    correction = PdfGlyphInspector().verify(inspection, {1: _backend_chars()}, [_block("6=")]).corrections[0]
    corrected, provenance = apply_verified_font_glyph_correction("6=", pdf_sha256=inspection.pdf_sha256, page=1, block=0, correction=correction)
    assert corrected == "≠"
    assert provenance["original_text"] == "6="
    try:
        apply_verified_font_glyph_correction("6=", pdf_sha256="different-document", page=1, block=0, correction=correction)
    except ValueError as exc:
        assert "source PDF SHA-256" in str(exc)
    else:
        raise AssertionError("stale font recovery must be rejected")


def test_cmap_retains_exact_code_ranges_and_flags_conflicting_mappings():
    cmap = _parse_tounicode(
        b"""1 begincodespacerange <0000> <00ff> endcodespacerange
        2 beginbfchar <0036> <0036> <0001> <0041> endbfchar
        2 beginbfchar <0001> <0042> endbfchar"""
    )
    assert cmap.code_space_ranges == ((b"\x00\x00", b"\x00\xff"),)
    assert cmap.mappings[b"\x006"] == "6"
    assert cmap.conflicts == (b"\x00\x01",)

    invalid = _parse_tounicode(b"1 beginbfchar <01> <D800> endbfchar")
    assert invalid.status.startswith("partial:")


def test_subset_names_math_glyphs_and_unavailable_names_are_conservative():
    assert normalized_font_name("/ABCDEF+LMMathSymbols10-Regular") == "LMMathSymbols10-Regular"
    assert glyph_name_to_unicode("element") == "∈"
    assert glyph_name_to_unicode("notequal") == "≠"
    assert glyph_name_to_unicode("negationslash") is None
    assert glyph_name_to_unicode("unknown.privateglyph") is None


def test_pdfium_rotation_and_crop_geometry_are_normalized_to_display_frame():
    geometry = PdfGlyphPageGeometry(1, 200.0, 100.0, (10.0, 20.0, 110.0, 220.0), 90)
    bbox, status = _to_display_bbox((20.0, 40.0, 30.0, 50.0), geometry)
    assert status == "normalized_display_frame"
    assert bbox == (0.1, 0.1, 0.15, 0.2)


def test_backend_alignment_handles_decomposed_unicode_and_common_ligature_segmentation():
    from deepdoc.parser.pdf_glyph_inspector import PdfGlyphCodeUse

    decomposed_use = PdfGlyphCodeUse(
        page=1,
        resource_name="/F",
        resource_scope="page:1:/F",
        font_fingerprint="fingerprint",
        operator_index=0,
        string_index=0,
        code_index=0,
        character_code=1,
        character_code_hex="01",
        code_bytes=b"\x01",
        cid=None,
        cid_status="not_applicable_simple_font",
        glyph_id=None,
        glyph_id_status="unavailable_without_cid",
        glyph_name=None,
        unicode_mapping="é",
        unicode_mapping_status="mapped",
        decoded_operand_text=None,
    )
    decomposed_char = PdfiumCharacter(1, 0, "e\u0301", (0.1, 0.1, 0.2, 0.2), "Font", "normalized_display_frame")
    assert _backend_character_matches(decomposed_use, decomposed_char)

    ligature_use = PdfGlyphCodeUse(
        page=1,
        resource_name="/F",
        resource_scope="page:1:/F",
        font_fingerprint="fingerprint",
        operator_index=0,
        string_index=0,
        code_index=0,
        character_code=2,
        character_code_hex="02",
        code_bytes=b"\x02",
        cid=None,
        cid_status="not_applicable_simple_font",
        glyph_id=None,
        glyph_id_status="unavailable_without_cid",
        glyph_name=None,
        unicode_mapping="fi",
        unicode_mapping_status="mapped",
        decoded_operand_text=None,
    )
    ligature_char = PdfiumCharacter(1, 0, "ﬁ", (0.1, 0.1, 0.2, 0.2), "Font", "normalized_display_frame")
    assert _backend_character_matches(ligature_use, ligature_char)
