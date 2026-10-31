"""Inspect PDF font mappings and retain source character codes when available.

The inspector deliberately separates PDF character codes, CIDs, GIDs and
Unicode. It never treats a font name or a valid-looking string as proof that
the extracted text is correct.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.generic import ByteStringObject, ContentStream, TextStringObject

from .pdf_glyph_backend import PdfiumCharacter
from .pdf_glyph_repair import VerifiedFontGlyphCorrection

_MAX_CMAP_ENTRIES = 100_000
_OVERLAY_GLYPHS = {"negationslash"}
_AGL_CORE = {
    "element": "∈",
    "equal": "=",
    "minus": "−",
    "multiply": "×",
    "notequal": "≠",
    "plusminus": "±",
    "six": "6",
}
_DIGIT_GLYPHS = dict(zip(("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"), "0123456789"))
_ALIGNMENT_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})


@dataclass(frozen=True, slots=True)
class PdfFontMetadata:
    page: int
    resource_name: str
    resource_ref: str | None
    scope: str
    base_font: str | None
    normalized_base_font: str | None
    subtype: str | None
    encoding_name: str | None
    encoding_status: str
    encoding_sha256: str
    differences: tuple[tuple[int, str], ...]
    descriptor: tuple[tuple[str, str | int | float | None], ...]
    embedded_font_status: str
    embedded_font_sha256: str | None
    to_unicode_status: str
    to_unicode_sha256: str | None
    to_unicode_entry_count: int
    to_unicode_conflicts: tuple[str, ...]
    font_fingerprint: str
    fingerprint_method: str
    cid_to_gid_status: str

    def metadata(self) -> dict:
        return {
            "page": self.page,
            "resource_name": self.resource_name,
            "resource_ref": self.resource_ref,
            "scope": self.scope,
            "base_font": self.base_font,
            "normalized_base_font": self.normalized_base_font,
            "subtype": self.subtype,
            "encoding_name": self.encoding_name,
            "encoding_status": self.encoding_status,
            "encoding_sha256": self.encoding_sha256,
            "differences": [{"character_code": code, "glyph_name": name} for code, name in self.differences],
            "descriptor": dict(self.descriptor),
            "embedded_font_status": self.embedded_font_status,
            "embedded_font_sha256": self.embedded_font_sha256,
            "to_unicode_status": self.to_unicode_status,
            "to_unicode_sha256": self.to_unicode_sha256,
            "to_unicode_entry_count": self.to_unicode_entry_count,
            "to_unicode_conflicts": list(self.to_unicode_conflicts),
            "font_fingerprint": self.font_fingerprint,
            "fingerprint_method": self.fingerprint_method,
            "cid_to_gid_status": self.cid_to_gid_status,
        }

    @property
    def difference_map(self) -> dict[int, str]:
        return dict(self.differences)


@dataclass(frozen=True, slots=True)
class PdfGlyphCodeUse:
    page: int
    resource_name: str
    resource_scope: str
    font_fingerprint: str
    operator_index: int
    string_index: int
    code_index: int
    character_code: int | None
    character_code_hex: str | None
    code_bytes: bytes | None
    cid: int | None
    cid_status: str
    glyph_id: int | None
    glyph_id_status: str
    glyph_name: str | None
    unicode_mapping: str | None
    unicode_mapping_status: str
    decoded_operand_text: str | None

    def metadata(self) -> dict:
        return {
            "page": self.page,
            "resource_name": self.resource_name,
            "resource_scope": self.resource_scope,
            "font_fingerprint": self.font_fingerprint,
            "operator_index": self.operator_index,
            "string_index": self.string_index,
            "code_index": self.code_index,
            "character_code": self.character_code,
            "character_code_hex": self.character_code_hex,
            "cid": self.cid,
            "cid_status": self.cid_status,
            "glyph_id": self.glyph_id,
            "glyph_id_status": self.glyph_id_status,
            "glyph_name": self.glyph_name,
            "unicode_mapping": self.unicode_mapping,
            "unicode_mapping_status": self.unicode_mapping_status,
            "decoded_operand_text": self.decoded_operand_text,
        }


@dataclass(frozen=True, slots=True)
class PdfGlyphInspection:
    pdf_sha256: str
    page_count: int
    font_resources: tuple[PdfFontMetadata, ...]
    code_uses: tuple[PdfGlyphCodeUse, ...]
    pages_to_verify: tuple[int, ...]
    diagnostics: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "pdf_sha256": self.pdf_sha256,
            "page_count": self.page_count,
            "font_resource_count": len(self.font_resources),
            "font_resources_with_embedded_program": sum(item.embedded_font_sha256 is not None for item in self.font_resources),
            "font_resources_with_to_unicode": sum(item.to_unicode_sha256 is not None for item in self.font_resources),
            "character_code_count": len(self.code_uses),
            "pages_sent_to_secondary_backend": list(self.pages_to_verify),
            "diagnostic_count": len(self.diagnostics),
        }

    def metadata(self) -> dict:
        return {
            **self.summary(),
            "fonts": [font.metadata() for font in self.font_resources],
            "glyph_uses": [use.metadata() for use in self.code_uses],
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class GlyphTextBlock:
    page: int
    block: int
    source_id: str
    text: str
    page_width: float
    page_height: float
    lines: tuple[str, ...]
    line_boxes: tuple[tuple[float, float, float, float] | None, ...]
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class PdfGlyphAnalysis:
    findings: tuple[dict, ...]
    corrections: tuple[VerifiedFontGlyphCorrection, ...]


@dataclass(frozen=True, slots=True)
class _CMap:
    mappings: Mapping[bytes, str]
    code_space_ranges: tuple[tuple[bytes, bytes], ...]
    conflicts: tuple[bytes, ...]
    status: str


@dataclass(frozen=True, slots=True)
class _FontState:
    metadata: PdfFontMetadata
    to_unicode: _CMap
    encoding_cid_map: Mapping[bytes, int]
    cid_to_gid: bytes | None
    identity_cid_encoding: bool


class PdfGlyphInspector:
    """Collect source-level font/glyph evidence without changing extracted text."""

    def inspect(
        self,
        data: bytes,
        *,
        suspicious_pages: set[int] | frozenset[int] = frozenset(),
        pages_to_inspect: set[int] | frozenset[int] | None = None,
    ) -> PdfGlyphInspection:
        if not isinstance(data, bytes) or not data:
            raise ValueError("PDF glyph inspection requires non-empty source bytes")
        pdf_sha256 = hashlib.sha256(data).hexdigest()
        font_rows: list[PdfFontMetadata] = []
        code_uses: list[PdfGlyphCodeUse] = []
        pages_to_verify: set[int] = set(suspicious_pages)
        diagnostics: list[dict] = []
        font_stream_hash_cache: dict[tuple[int, int] | int, str] = {}

        try:
            with PdfReader(BytesIO(data), strict=False) as reader:
                page_count = len(reader.pages)
                selected_pages = set(range(1, page_count + 1)) if pages_to_inspect is None else set(pages_to_inspect)
                selected_pages.update(suspicious_pages)
                if any(type(number) is not int or number < 1 or number > page_count for number in selected_pages):
                    raise ValueError("font inspection page selection is outside the source PDF")
                for page_number, page in enumerate(reader.pages, start=1):
                    if page_number not in selected_pages:
                        continue
                    font_states = _page_font_states(page, page_number, pdf_sha256, font_stream_hash_cache=font_stream_hash_cache)
                    suspect_resources = {
                        name
                        for name, state in font_states.items()
                        if any(glyph_name.lstrip("/") in _OVERLAY_GLYPHS for _, glyph_name in state.metadata.differences)
                        or state.metadata.to_unicode_conflicts
                        or state.to_unicode.status.startswith("partial:")
                    }
                    if suspect_resources:
                        equal_resources = {name for name, state in font_states.items() if any(glyph_name.lstrip("/") == "equal" for _, glyph_name in state.metadata.differences)}
                        font_states = _page_font_states(
                            page,
                            page_number,
                            pdf_sha256,
                            font_stream_hash_cache=font_stream_hash_cache,
                            hash_resources=suspect_resources | equal_resources,
                        )
                    font_rows.extend(state.metadata for state in font_states.values())
                    content_resources = suspect_resources or (set(font_states) if page_number in suspicious_pages else set())
                    if not content_resources:
                        continue
                    uses, content_diagnostics = _read_page_code_uses(page, page_number, reader, font_states, content_resources)
                    code_uses.extend(uses)
                    diagnostics.extend(content_diagnostics)
                    page_has_mapping_finding = page_number in suspicious_pages
                    for use in uses:
                        state = font_states.get(use.resource_name)
                        if state is None:
                            continue
                        glyph_name = use.glyph_name
                        mapped_name = glyph_name_to_unicode(glyph_name) if glyph_name else None
                        explicit_overlay = bool(glyph_name) and glyph_name.lstrip("/") in _OVERLAY_GLYPHS
                        cmap_conflict = use.code_bytes in state.to_unicode.conflicts if use.code_bytes is not None else False
                        if explicit_overlay or cmap_conflict or (mapped_name is not None and use.unicode_mapping is not None and mapped_name != use.unicode_mapping):
                            page_has_mapping_finding = True
                            diagnostics.append(
                                {
                                    "code": "PDF_FONT_GLYPH_MAPPING_SUSPECT",
                                    "status": "SUSPECT",
                                    "page": page_number,
                                    "font_resource": use.resource_name,
                                    "font_fingerprint": use.font_fingerprint,
                                    "character_code": use.character_code,
                                    "character_code_hex": use.character_code_hex,
                                    "cid": use.cid,
                                    "cid_status": use.cid_status,
                                    "glyph_id": use.glyph_id,
                                    "glyph_id_status": use.glyph_id_status,
                                    "glyph_name": glyph_name,
                                    "unicode_mapping": use.unicode_mapping,
                                    "glyph_name_unicode": mapped_name,
                                    "decoded_operand_text": use.decoded_operand_text,
                                    "source_backend": "pypdf_content_stream_and_font_resources",
                                    "location_resolution": "character_code_without_geometry",
                                    "reason": "font_mapping_evidence_requires_visual-or-independent-backend-check",
                                }
                            )
                    if page_has_mapping_finding:
                        pages_to_verify.add(page_number)
        except Exception as exc:  # noqa: BLE001 - malformed PDF objects can surface varied pypdf errors.
            diagnostics.append(
                {
                    "code": "PDF_FONT_INSPECTION_UNAVAILABLE",
                    "status": "SUSPECT",
                    "source_backend": "pypdf",
                    "reason": f"{type(exc).__name__}: {exc}"[:1000],
                }
            )
            return PdfGlyphInspection(pdf_sha256, 0, tuple(font_rows), tuple(code_uses), tuple(sorted(pages_to_verify)), tuple(diagnostics))

        # Keep the fully hashed record if a suspicious mapping caused a scoped
        # font-program inspection.
        unique_fonts: dict[tuple[int, str], PdfFontMetadata] = {}
        for item in font_rows:
            key = (item.page, item.resource_name)
            if key not in unique_fonts or item.embedded_font_sha256 is not None:
                unique_fonts[key] = item
        return PdfGlyphInspection(
            pdf_sha256,
            page_count,
            tuple(unique_fonts[key] for key in sorted(unique_fonts)),
            tuple(code_uses),
            tuple(sorted(pages_to_verify)),
            tuple(diagnostics),
        )

    def verify(
        self,
        inspection: PdfGlyphInspection,
        backend_pages: Mapping[int, tuple[PdfiumCharacter, ...]],
        blocks: tuple[GlyphTextBlock, ...] | list[GlyphTextBlock],
    ) -> PdfGlyphAnalysis:
        """Align flagged source codes to PDFium characters and propose repairs.

        A correction is emitted only for an explicit ``negationslash`` source
        glyph overprinting a separately evidenced equals glyph, with one-to-one
        code/font alignment, character boxes, and an exact matching text span.
        Backend agreement alone never yields VERIFIED.
        """
        fonts_by_page: dict[int, list[PdfFontMetadata]] = defaultdict(list)
        uses_by_font: dict[tuple[int, str, str], list[PdfGlyphCodeUse]] = defaultdict(list)
        blocks_by_page: dict[int, list[GlyphTextBlock]] = defaultdict(list)
        for font in inspection.font_resources:
            fonts_by_page[font.page].append(font)
        for use in inspection.code_uses:
            uses_by_font[(use.page, use.resource_name, use.font_fingerprint)].append(use)
        for block in blocks:
            blocks_by_page[block.page].append(block)

        findings: list[dict] = []
        corrections: list[VerifiedFontGlyphCorrection] = []
        for page_number in inspection.pages_to_verify:
            page_fonts = fonts_by_page.get(page_number, [])
            chars = backend_pages.get(page_number)
            if chars is None:
                findings.append(
                    {
                        "code": "PDF_GLYPH_SECONDARY_BACKEND_UNAVAILABLE",
                        "status": "SUSPECT",
                        "page": page_number,
                        "source_backend": "pypdfium2",
                        "location_resolution": "page",
                        "reason": "no secondary backend result for the selected page",
                    }
                )
                continue

            chars_by_font: dict[str | None, list[PdfiumCharacter]] = defaultdict(list)
            for char in chars:
                chars_by_font[normalized_font_name(char.font_name)].append(char)
            aligned: dict[tuple[str, int, int, int], tuple[PdfiumCharacter, PdfFontMetadata]] = {}
            for (use_page, resource, fingerprint), uses in uses_by_font.items():
                if use_page != page_number:
                    continue
                resource_fonts = [font for font in page_fonts if font.resource_name == resource and font.font_fingerprint == fingerprint]
                if len(resource_fonts) != 1:
                    continue
                font = resource_fonts[0]
                font_name = font.normalized_base_font
                font_chars = chars_by_font.get(font_name, [])
                same_name_resources = [item for item in page_fonts if item.normalized_base_font == font_name]
                # PDFium reports a font name, not the PDF resource key. If that
                # name selects different mappings on this page, resource-level
                # alignment is ambiguous and must stop here.
                if len({item.font_fingerprint for item in same_name_resources}) != 1 or len(uses) != len(font_chars):
                    continue
                paired = list(zip(uses, font_chars, strict=True))
                if not all(_backend_character_matches(use, char) for use, char in paired):
                    continue
                for use, char in paired:
                    aligned[_code_use_key(use)] = (char, font)

            overlays = [use for use in inspection.code_uses if use.page == page_number and use.glyph_name and use.glyph_name.lstrip("/") in _OVERLAY_GLYPHS]
            line_assignments: dict[tuple[str, int], list[tuple]] = defaultdict(list)
            pair_records: dict[tuple[str, int, int, int], tuple] = {}
            unresolved: dict[tuple[str, int, int, int], tuple[PdfGlyphCodeUse, PdfiumCharacter | None, str]] = {}
            for use in overlays:
                key = _code_use_key(use)
                alignment = aligned.get(key)
                if alignment is None or alignment[0].bbox is None:
                    source_font = next((font for font in page_fonts if font.resource_name == use.resource_name), None)
                    same_name_resources = [font for font in page_fonts if source_font and font.normalized_base_font == source_font.normalized_base_font]
                    conflict = len({font.font_fingerprint for font in same_name_resources}) > 1
                    reason = "PDFium font name maps to multiple PDF resources with different encodings" if conflict else "font code could not be uniquely aligned to PDFium character geometry"
                    unresolved[key] = (use, alignment[0] if alignment else None, reason)
                    continue
                source_char, source_font = alignment
                equal_candidates = []
                equal_mapping_conflict = False
                for char in chars:
                    if char.text != "=" or char.bbox is None:
                        continue
                    equal_fonts = _font_mapping_for_backend_char(page_fonts, char.font_name, "equal", "=")
                    if equal_fonts is None:
                        normalized_name = normalized_font_name(char.font_name)
                        equal_mapping_conflict |= any(
                            font.normalized_base_font == normalized_name and font.embedded_font_sha256 is not None and any(name.lstrip("/") == "equal" for _, name in font.differences)
                            for font in page_fonts
                        )
                        continue
                    overlap = _bbox_overlap_ratio(source_char.bbox, char.bbox)
                    if overlap >= 0.5 and _centers_coincide(source_char.bbox, char.bbox):
                        equal_candidates.append((char, equal_fonts, overlap))
                if len(equal_candidates) != 1:
                    reason = "negation glyph has no unique, geometry-overlapping equals glyph"
                    unresolved[key] = (use, source_char, reason)
                    if equal_mapping_conflict:
                        unresolved[key] = (use, source_char, "PDFium equals font name maps to conflicting embedded font encodings")
                    continue
                equal_char, equal_fonts, overlap = equal_candidates[0]
                pair = (use, source_char, source_font, equal_char, equal_fonts, overlap)
                pair_records[key] = pair
                if not use.decoded_operand_text or len(use.decoded_operand_text) != 1:
                    unresolved[key] = (use, source_char, "source character code cannot be matched to an exact Poppler text span")
                    continue
                pattern = re.compile(re.escape(use.decoded_operand_text) + r"\s*=")
                locations = []
                for block in blocks_by_page.get(page_number, []):
                    normalized_block = _absolute_to_normalized(block.bbox, block.page_width, block.page_height)
                    if not _box_contains(normalized_block, source_char.bbox) or not _box_contains(normalized_block, equal_char.bbox):
                        continue
                    for line_index, (line_text, line_box) in enumerate(zip(block.lines, block.line_boxes, strict=True)):
                        normalized_line = _absolute_to_normalized(line_box, block.page_width, block.page_height)
                        if not _box_contains(normalized_line, source_char.bbox) or not _box_contains(normalized_line, equal_char.bbox):
                            continue
                        spans = list(pattern.finditer(line_text))
                        if spans:
                            locations.append((block, line_index, line_text, spans))
                if len(locations) != 1:
                    reason = "glyph geometry did not resolve to exactly one Poppler text line"
                    unresolved[key] = (use, source_char, reason)
                    continue
                block, line_index, line_text, spans = locations[0]
                line_assignments[(block.source_id, line_index)].append((block, line_index, line_text, spans, pair))

            by_block: dict[tuple[int, int, str], dict] = {}
            verified_keys: set[tuple[str, int, int, int]] = set()
            for group in line_assignments.values():
                block, line_index, line_text, spans = group[0][:4]
                pairs = [item[4] for item in group]
                # All occurrences of this ASCII shape on the line must have an
                # independent overlaid-font pair. A normal literal ``6=`` on
                # the same line therefore prevents an ambiguous automatic edit.
                if len(spans) != len(pairs):
                    for pair in pairs:
                        use, source_char = pair[0], pair[1]
                        unresolved[_code_use_key(use)] = (use, source_char, "Poppler candidate count differs from source-font overlay count")
                    continue
                ordered_pairs = sorted(pairs, key=lambda item: (item[1].bbox[0], item[1].bbox[1]))
                ordered_spans = sorted(spans, key=lambda span: span.start())
                if any(pair[1].bbox[0] > pair[3].bbox[0] + 0.02 for pair in ordered_pairs):
                    for pair in ordered_pairs:
                        unresolved[_code_use_key(pair[0])] = (pair[0], pair[1], "negation/equality glyph ordering is ambiguous")
                    continue
                line_offset = sum(len(line) + 1 for line in block.lines[:line_index])
                target = by_block.setdefault((block.page, block.block, block.source_id), {"block": block, "spans": [], "evidence": [], "fingerprints": set()})
                for span, pair in zip(ordered_spans, ordered_pairs, strict=True):
                    use, source_char, source_font, equal_char, equal_fonts, overlap = pair
                    target["spans"].append((line_offset + span.start(), line_offset + span.end(), span.group(0), "≠"))
                    target["fingerprints"].add(source_font.font_fingerprint)
                    target["fingerprints"].update(font.font_fingerprint for font in equal_fonts)
                    target["evidence"].append(
                        {
                            "source_backend": "pypdf_content_stream_and_font_resources",
                            "secondary_backend": "pypdfium2",
                            "page": page_number,
                            "resource_scope": source_font.scope,
                            "character_code_hex": use.character_code_hex,
                            "cid": use.cid,
                            "cid_status": use.cid_status,
                            "glyph_id": use.glyph_id,
                            "glyph_id_status": use.glyph_id_status,
                            "glyph_name": use.glyph_name,
                            "source_unicode_mapping": use.unicode_mapping,
                            "embedded_font_sha256": source_font.embedded_font_sha256,
                            "font_fingerprint": source_font.font_fingerprint,
                            "to_unicode_status": source_font.to_unicode_status,
                            "pdfium_text": source_char.text,
                            "pdfium_font_name": source_char.font_name,
                            "glyph_bbox_normalized": list(source_char.bbox),
                            "equals_text": equal_char.text,
                            "equals_font_name": equal_char.font_name,
                            "equals_font_candidates": [font.resource_name for font in equal_fonts],
                            "equals_font_fingerprints": [font.font_fingerprint for font in equal_fonts],
                            "equals_font_program_hashes": [font.embedded_font_sha256 for font in equal_fonts],
                            "equals_character_code_glyph_name": "/equal",
                            "equals_bbox_normalized": list(equal_char.bbox),
                            "overlap_ratio": round(overlap, 6),
                            "observation_source_id": block.source_id,
                            "location_resolution": "character_bbox",
                            "raw_block_text": block.text,
                        }
                    )
                    verified_keys.add(_code_use_key(use))

            for (_page, block_number, _source_id), item in by_block.items():
                block = item["block"]
                correction = VerifiedFontGlyphCorrection(
                    pdf_sha256=inspection.pdf_sha256,
                    page=page_number,
                    block=block_number,
                    original_text=block.text,
                    spans=tuple(item["spans"]),
                    font_fingerprints=tuple(sorted(item["fingerprints"])),
                    evidence=tuple(item["evidence"]),
                    method="font_glyph_name_negationslash_overlaid_equal",
                )
                corrections.append(correction)

            for key, (use, char, reason) in unresolved.items():
                status = "CONFLICT" if any(marker in reason for marker in ("differs", "ambiguous", "conflicting", "multiple PDF resources")) else "SUSPECT"
                findings.append(_finding(use, char, status, reason))
            for key in verified_keys:
                use, source_char, *_ = pair_records[key]
                findings.append(_finding(use, source_char, "VERIFIED", "font component glyph is geometrically overlaid with a separately mapped equals glyph"))

        return PdfGlyphAnalysis(tuple(findings), tuple(corrections))


def detect_text_anomalies(blocks: tuple[GlyphTextBlock, ...] | list[GlyphTextBlock]) -> tuple[dict, ...]:
    """Flag explicit extraction sentinels without treating rare valid text as bad."""
    findings = []
    cid_placeholder = re.compile(r"(?i)(?:\bCID\s*[:+#-]?\s*\d+\b|<\s*cid\s*[:+#-]?\s*\d+\s*>)")
    for block in blocks:
        for index, character in enumerate(block.text):
            codepoint = ord(character)
            category = unicodedata.category(character)
            code = None
            reason = None
            if codepoint == 0xFFFD:
                code, reason = "PDF_TEXT_REPLACEMENT_CHARACTER", "Unicode replacement character in native extraction"
            elif 0xE000 <= codepoint <= 0xF8FF or 0xF0000 <= codepoint <= 0xFFFFD or 0x100000 <= codepoint <= 0x10FFFD:
                code, reason = "PDF_TEXT_PRIVATE_USE_CHARACTER", "private-use Unicode value needs font mapping review"
            elif category == "Cc" and character not in "\t\r\n\f":
                code, reason = "PDF_TEXT_CONTROL_CHARACTER", "unexpected control character in native extraction"
            if code:
                findings.append(
                    {
                        "code": code,
                        "status": "SUSPECT",
                        "page": block.page,
                        "block": block.block,
                        "source_id": block.source_id,
                        "character_index": index,
                        "unicode_codepoint": f"U+{codepoint:04X}",
                        "raw_text": block.text[:1000],
                        "bbox": list(block.bbox) if block.bbox else None,
                        "source_backend": "poppler-pdftotext",
                        "location_resolution": "block_with_text_character_index",
                        "secondary_verification_recommended": code == "PDF_TEXT_REPLACEMENT_CHARACTER",
                        "reason": reason,
                    }
                )
        for match in cid_placeholder.finditer(block.text):
            findings.append(
                {
                    "code": "PDF_TEXT_CID_PLACEHOLDER",
                    "status": "SUSPECT",
                    "page": block.page,
                    "block": block.block,
                    "source_id": block.source_id,
                    "character_span": [match.start(), match.end()],
                    "raw_text": block.text[:1000],
                    "bbox": list(block.bbox) if block.bbox else None,
                    "source_backend": "poppler-pdftotext",
                    "location_resolution": "block_text_span",
                    "secondary_verification_recommended": True,
                    "reason": "CID placeholder string surfaced in native extraction",
                }
            )
    return tuple(findings)


def _code_use_key(use: PdfGlyphCodeUse) -> tuple[str, int, int, int]:
    return use.resource_name, use.operator_index, use.string_index, use.code_index


def _backend_character_matches(use: PdfGlyphCodeUse, char: PdfiumCharacter) -> bool:
    expected = use.unicode_mapping or glyph_name_to_unicode(use.glyph_name)
    if expected is not None:
        return _alignment_text(char.text) == _alignment_text(expected)
    return bool(use.decoded_operand_text) and _alignment_text(char.text) == _alignment_text(use.decoded_operand_text)


def _alignment_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.translate(_ALIGNMENT_LIGATURES))


def _font_mapping_for_backend_char(fonts: list[PdfFontMetadata], backend_font_name: str | None, glyph_name: str, expected: str) -> tuple[PdfFontMetadata, ...] | None:
    name = normalized_font_name(backend_font_name)
    candidates = [
        font
        for font in fonts
        if font.normalized_base_font == name
        and font.embedded_font_sha256 is not None
        and expected in {glyph_name_to_unicode(value) for value in font.difference_map.values()}
        and glyph_name.lstrip("/") in {value.lstrip("/") for value in font.difference_map.values()}
    ]
    program_hashes = {font.embedded_font_sha256 for font in candidates}
    equivalent_codes = {tuple(sorted(code for code, value in font.differences if value.lstrip("/") == glyph_name.lstrip("/"))) for font in candidates}
    if not candidates or len(program_hashes) != 1 or len(equivalent_codes) != 1:
        return None
    return tuple(candidates)


def _finding(use: PdfGlyphCodeUse, char: PdfiumCharacter | None, status: str, reason: str) -> dict:
    return {
        "code": "PDF_FONT_GLYPH_RECOVERY_" + status,
        "status": status,
        "page": use.page,
        "font_resource": use.resource_name,
        "resource_scope": use.resource_scope,
        "font_fingerprint": use.font_fingerprint,
        "character_code": use.character_code,
        "character_code_hex": use.character_code_hex,
        "cid": use.cid,
        "cid_status": use.cid_status,
        "glyph_id": use.glyph_id,
        "glyph_id_status": use.glyph_id_status,
        "glyph_name": use.glyph_name,
        "raw_extracted_text": use.decoded_operand_text,
        "secondary_text": char.text if char else None,
        "bbox": list(char.bbox) if char and char.bbox else None,
        "source_backend": "pypdf_content_stream_and_font_resources",
        "secondary_backend": "pypdfium2",
        "location_resolution": "character_bbox" if char and char.bbox else "character_code_without_geometry",
        "reason": reason,
    }


def _bbox_overlap_ratio(left: tuple[float, float, float, float] | None, right: tuple[float, float, float, float] | None) -> float:
    if left is None or right is None:
        return 0.0
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_left = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    area_right = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    return intersection / min(area_left, area_right) if min(area_left, area_right) > 0 else 0.0


def _centers_coincide(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    left_x, left_y = (left[0] + left[2]) / 2, (left[1] + left[3]) / 2
    right_x, right_y = (right[0] + right[2]) / 2, (right[1] + right[3]) / 2
    width = max(left[2] - left[0], right[2] - right[0])
    height = max(left[3] - left[1], right[3] - right[1])
    return abs(left_x - right_x) <= max(width * 0.2, 0.002) and abs(left_y - right_y) <= max(height * 0.2, 0.002)


def _box_contains(container: tuple[float, float, float, float] | None, contained: tuple[float, float, float, float] | None) -> bool:
    if container is None or contained is None:
        return False
    tolerance = 0.01
    return contained[0] >= container[0] - tolerance and contained[1] >= container[1] - tolerance and contained[2] <= container[2] + tolerance and contained[3] <= container[3] + tolerance


def _absolute_to_normalized(bbox: tuple[float, float, float, float] | None, page_width: float, page_height: float) -> tuple[float, float, float, float] | None:
    if bbox is None or page_width <= 0 or page_height <= 0:
        return None
    x0, y0, x1, y1 = bbox
    return x0 / page_width, y0 / page_height, x1 / page_width, y1 / page_height


def glyph_name_to_unicode(name: str | None) -> str | None:
    """Return a conservative Adobe glyph-name mapping for common glyphs.

    Unknown names stay unknown. ``negationslash`` is a component glyph used by
    some math fonts and does not have a standalone AGL Unicode mapping.
    """
    if not name:
        return None
    name = name.lstrip("/").split(".", 1)[0]
    if name in _AGL_CORE:
        return _AGL_CORE[name]
    if name in _DIGIT_GLYPHS:
        return _DIGIT_GLYPHS[name]
    if len(name) == 1 and name.isascii() and name.isalnum():
        return name
    if name.startswith("uni") and len(name) > 3 and len(name[3:]) % 4 == 0:
        try:
            return bytes.fromhex(name[3:]).decode("utf-16-be", errors="strict")
        except (ValueError, OverflowError, UnicodeDecodeError):
            return None
    if name.startswith("u") and 5 <= len(name) <= 7:
        try:
            codepoint = int(name[1:], 16)
            return chr(codepoint) if codepoint <= 0x10FFFF and not 0xD800 <= codepoint <= 0xDFFF else None
        except (ValueError, OverflowError):
            return None
    return None


def normalized_font_name(name: str | None) -> str | None:
    if not name:
        return None
    normalized = name.lstrip("/")
    return re.sub(r"^[A-Z]{6}\+", "", normalized)


def _page_font_states(
    page,
    page_number: int,
    pdf_sha256: str,
    *,
    font_stream_hash_cache: dict[tuple[int, int] | int, str],
    hash_resources: set[str] | None = None,
) -> dict[str, _FontState]:
    resources = page.get("/Resources")
    if resources is None:
        return {}
    resources = resources.get_object()
    font_dictionary = resources.get("/Font")
    if font_dictionary is None:
        return {}
    font_dictionary = font_dictionary.get_object()
    output = {}
    for resource_name, reference in font_dictionary.items():
        font = reference.get_object()
        if not isinstance(font, Mapping):
            continue
        resource_key = str(resource_name)
        output[resource_key] = _font_state(
            font,
            reference,
            page_number,
            resource_key,
            pdf_sha256,
            force_hash=hash_resources is not None and resource_key in hash_resources,
            font_stream_hash_cache=font_stream_hash_cache,
        )
    return output


def _font_state(
    font,
    reference,
    page_number: int,
    resource_name: str,
    pdf_sha256: str,
    *,
    force_hash: bool,
    font_stream_hash_cache: dict[tuple[int, int] | int, str],
) -> _FontState:
    base_font = _pdf_name(font.get("/BaseFont"))
    normalized_base = normalized_font_name(base_font)
    subtype = _pdf_name(font.get("/Subtype"))
    encoding = font.get("/Encoding")
    encoding_obj = encoding.get_object() if hasattr(encoding, "get_object") else encoding
    differences = _encoding_differences(encoding_obj)
    encoding_name = _pdf_name(encoding_obj) if not isinstance(encoding_obj, Mapping) else _pdf_name(encoding_obj.get("/BaseEncoding"))
    encoding_status = "available" if encoding_name or differences else "not_specified"
    encoding_material = {"name": encoding_name, "differences": differences}
    if isinstance(encoding_obj, Mapping):
        encoding_material["base_encoding"] = _pdf_name(encoding_obj.get("/BaseEncoding"))
        try:
            encoding_material["encoding_cmap_sha256"] = hashlib.sha256(encoding_obj.get_data()).hexdigest()
        except Exception:  # noqa: BLE001 - record unreadable optional encoding data and continue fail-closed.
            encoding_material["encoding_cmap_status"] = "unavailable"
    encoding_bytes = json.dumps(encoding_material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    encoding_sha256 = hashlib.sha256(encoding_bytes).hexdigest()

    descendant = None
    descendants = font.get("/DescendantFonts")
    if descendants:
        descendant = descendants[0].get_object()
    descriptor_ref = font.get("/FontDescriptor") or (descendant.get("/FontDescriptor") if descendant else None)
    descriptor = descriptor_ref.get_object() if descriptor_ref is not None and hasattr(descriptor_ref, "get_object") else descriptor_ref
    descriptor_fields = _descriptor_fields(descriptor)

    embedded_font_sha256 = None
    embedded_font_status = "not_embedded"
    if descriptor is not None:
        stream_key = next((key for key in ("/FontFile", "/FontFile2", "/FontFile3") if descriptor.get(key) is not None), None)
        if stream_key:
            embedded_font_status = "present_not_hashed"
            if force_hash:
                try:
                    stream_ref = descriptor[stream_key]
                    cache_key = (stream_ref.idnum, stream_ref.generation) if hasattr(stream_ref, "idnum") else id(stream_ref)
                    embedded_font_sha256 = font_stream_hash_cache.get(cache_key)
                    if embedded_font_sha256 is None:
                        stream = stream_ref.get_object() if hasattr(stream_ref, "get_object") else stream_ref
                        embedded_font_sha256 = hashlib.sha256(stream.get_data()).hexdigest()
                        font_stream_hash_cache[cache_key] = embedded_font_sha256
                    embedded_font_status = "available"
                except Exception:  # noqa: BLE001 - unreadable embedded font streams are diagnostic-only.
                    embedded_font_status = "unreadable"

    to_unicode_ref = font.get("/ToUnicode")
    to_unicode_data = None
    to_unicode_status = "missing"
    if to_unicode_ref is not None:
        try:
            to_unicode_data = to_unicode_ref.get_object().get_data()
            to_unicode_status = "available"
        except Exception:  # noqa: BLE001 - malformed ToUnicode streams must not abort native extraction.
            to_unicode_status = "unreadable"
    cmap = _parse_tounicode(to_unicode_data) if to_unicode_data is not None else _CMap({}, (), (), "missing")
    to_unicode_sha256 = hashlib.sha256(to_unicode_data).hexdigest() if to_unicode_data is not None else None

    resource_id = None
    if hasattr(reference, "idnum"):
        resource_id = f"{reference.idnum}:{getattr(reference, 'generation', 0)}"
    scope = f"page:{page_number}:{resource_name}"
    fingerprint_method = "embedded_font_stream+encoding+cmap" if embedded_font_sha256 else "document_scoped_resource_metadata+encoding+cmap"
    fingerprint_material = {
        "font_program_sha256": embedded_font_sha256,
        "encoding_sha256": encoding_sha256,
        "to_unicode_sha256": to_unicode_sha256,
    }
    if not embedded_font_sha256:
        fingerprint_material.update({"pdf_sha256": pdf_sha256, "scope": scope, "base_font": base_font, "subtype": subtype})
    fingerprint = hashlib.sha256(json.dumps(fingerprint_material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    cid_to_gid = None
    cid_to_gid_status = "not_applicable"
    if descendant is not None and _pdf_name(descendant.get("/Subtype")) == "/CIDFontType2":
        mapping = descendant.get("/CIDToGIDMap")
        if mapping is None:
            cid_to_gid_status = "unavailable"
        elif _pdf_name(mapping) == "/Identity":
            cid_to_gid_status = "identity"
        else:
            try:
                cid_to_gid = mapping.get_object().get_data()
                cid_to_gid_status = "stream_available"
            except Exception:  # noqa: BLE001 - malformed CID maps remain unavailable rather than inferred.
                cid_to_gid_status = "unreadable"

    metadata = PdfFontMetadata(
        page=page_number,
        resource_name=resource_name,
        resource_ref=resource_id,
        scope=scope,
        base_font=base_font,
        normalized_base_font=normalized_base,
        subtype=subtype,
        encoding_name=encoding_name,
        encoding_status=encoding_status,
        encoding_sha256=encoding_sha256,
        differences=tuple(differences),
        descriptor=tuple(descriptor_fields),
        embedded_font_status=embedded_font_status,
        embedded_font_sha256=embedded_font_sha256,
        to_unicode_status=to_unicode_status,
        to_unicode_sha256=to_unicode_sha256,
        to_unicode_entry_count=len(cmap.mappings),
        to_unicode_conflicts=tuple(code.hex() for code in cmap.conflicts),
        font_fingerprint=fingerprint,
        fingerprint_method=fingerprint_method,
        cid_to_gid_status=cid_to_gid_status,
    )
    encoding_cid_map = _parse_cid_encoding_cmap(encoding_obj) if isinstance(encoding_obj, Mapping) else {}
    identity_cid = _pdf_name(encoding_obj) in {"/Identity-H", "/Identity-V"}
    return _FontState(metadata, cmap, encoding_cid_map, cid_to_gid, identity_cid)


def _read_page_code_uses(page, page_number: int, reader, fonts: Mapping[str, _FontState], relevant_resources: set[str]) -> tuple[list[PdfGlyphCodeUse], list[dict]]:
    content = page.get_contents()
    if content is None:
        return [], []
    try:
        stream = ContentStream(content, reader)
    except Exception as exc:  # noqa: BLE001 - malformed content streams have inconsistent pypdf errors.
        return [], [{"code": "PDF_FONT_CONTENT_STREAM_UNAVAILABLE", "page": page_number, "reason": f"{type(exc).__name__}: {exc}"[:500]}]

    uses: list[PdfGlyphCodeUse] = []
    diagnostics: list[dict] = []
    current_font = None
    for operator_index, (operands, operator) in enumerate(stream.operations):
        op = bytes(operator).decode("latin1")
        if op == "Tf" and operands:
            current_font = str(operands[0])
            continue
        if op not in {"Tj", "TJ", "'", '"'} or current_font not in fonts or current_font not in relevant_resources:
            continue
        value = operands[-1] if op == '"' and operands else operands[0] if operands else None
        strings = value if isinstance(value, list) else [value]
        string_index = 0
        for item in strings:
            if not isinstance(item, (TextStringObject, ByteStringObject)):
                continue
            try:
                raw = bytes(item.original_bytes)
            except (AttributeError, TypeError, ValueError):
                diagnostics.append({"code": "PDF_FONT_CHARACTER_CODE_UNAVAILABLE", "page": page_number, "font_resource": current_font, "operator_index": operator_index})
                continue
            state = fonts[current_font]
            decoded = str(item)
            codes = _split_codes(raw, state)
            if not codes:
                diagnostics.append({"code": "PDF_FONT_CHARACTER_CODE_UNAVAILABLE", "page": page_number, "font_resource": current_font, "operator_index": operator_index})
                string_index += 1
                continue
            decoded_by_code = list(decoded) if len(decoded) == len(codes) else [None] * len(codes)
            for code_index, (code, status) in enumerate(codes):
                char_code = int.from_bytes(code, "big") if code else None
                glyph_name = state.metadata.difference_map.get(char_code) if char_code is not None else None
                unicode_value = state.to_unicode.mappings.get(code)
                cid, cid_status = _cid_for_code(code, state)
                glyph_id, glyph_id_status = _gid_for_cid(cid, state)
                uses.append(
                    PdfGlyphCodeUse(
                        page=page_number,
                        resource_name=current_font,
                        resource_scope=state.metadata.scope,
                        font_fingerprint=state.metadata.font_fingerprint,
                        operator_index=operator_index,
                        string_index=string_index,
                        code_index=code_index,
                        character_code=char_code,
                        character_code_hex=code.hex().upper() if code else None,
                        code_bytes=code or None,
                        cid=cid,
                        cid_status=cid_status,
                        glyph_id=glyph_id,
                        glyph_id_status=glyph_id_status,
                        glyph_name=glyph_name,
                        unicode_mapping=unicode_value,
                        unicode_mapping_status="mapped" if unicode_value is not None else "not_mapped_or_unavailable",
                        decoded_operand_text=decoded_by_code[code_index],
                    )
                )
            string_index += 1
    return uses, diagnostics


def _split_codes(raw: bytes, state: _FontState) -> list[tuple[bytes, str]]:
    metadata = state.metadata
    if metadata.subtype != "/Type0":
        return [(bytes((value,)), "single_byte_character_code") for value in raw]
    lengths = sorted({len(start) for start, _end in state.to_unicode.code_space_ranges}, reverse=True)
    if not lengths and state.identity_cid_encoding:
        lengths = [2]
    if not lengths and state.to_unicode.mappings:
        lengths = sorted({len(code) for code in state.to_unicode.mappings}, reverse=True)
    if not lengths:
        return []
    output = []
    offset = 0
    while offset < len(raw):
        selected = None
        for length in lengths:
            candidate = raw[offset : offset + length]
            if len(candidate) != length:
                continue
            if _in_codespace(candidate, state.to_unicode, identity=state.identity_cid_encoding):
                selected = candidate
                break
        if selected is None:
            return []
        output.append((selected, "cmap_codespace"))
        offset += len(selected)
    return output


def _cid_for_code(code: bytes, state: _FontState) -> tuple[int | None, str]:
    if state.metadata.subtype != "/Type0":
        return None, "not_applicable_simple_font"
    if state.identity_cid_encoding:
        return int.from_bytes(code, "big"), "identity_encoding_cmap"
    cid = state.encoding_cid_map.get(code)
    return (cid, "encoding_cmap") if cid is not None else (None, "unavailable")


def _gid_for_cid(cid: int | None, state: _FontState) -> tuple[int | None, str]:
    if cid is None:
        return None, "unavailable_without_cid"
    if state.metadata.cid_to_gid_status == "identity":
        return cid, "identity_cid_to_gid_map"
    if state.cid_to_gid is None:
        return None, "unavailable"
    offset = 2 * cid
    if offset + 2 > len(state.cid_to_gid):
        return None, "cid_outside_mapping_stream"
    return int.from_bytes(state.cid_to_gid[offset : offset + 2], "big"), "cid_to_gid_stream"


def _encoding_differences(encoding) -> list[tuple[int, str]]:
    if not isinstance(encoding, Mapping):
        return []
    values = encoding.get("/Differences")
    if values is None:
        return []
    output = []
    code = None
    for item in values:
        if isinstance(item, int):
            code = int(item)
            continue
        if code is None or not 0 <= code <= 255:
            continue
        output.append((code, _pdf_name(item) or str(item)))
        code += 1
    return output


def _parse_tounicode(data: bytes | None) -> _CMap:
    if data is None:
        return _CMap({}, (), (), "missing")
    text = re.sub(r"%[^\r\n]*", "", data.decode("latin1", errors="replace"))
    mappings: dict[bytes, str] = {}
    conflicts = set()
    code_space_ranges: set[tuple[bytes, bytes]] = set()
    count = 0

    def add(source_hex: str, target_hex: str) -> None:
        nonlocal count
        source_hex = re.sub(r"\s+", "", source_hex)
        target_hex = re.sub(r"\s+", "", target_hex)
        if len(source_hex) % 2 or len(target_hex) % 2:
            raise ValueError("CMap contains odd-length hexadecimal code")
        source, target = bytes.fromhex(source_hex), bytes.fromhex(target_hex)
        if not source or len(source) > 4:
            raise ValueError("CMap source code length is outside the supported range")
        if not target or len(target) % 2:
            raise ValueError("CMap Unicode destination is not valid UTF-16BE")
        unicode_value = target.decode("utf-16-be", errors="strict")
        previous = mappings.get(source)
        if previous is not None and previous != unicode_value:
            conflicts.add(source)
        else:
            mappings[source] = unicode_value
        count += 1
        if count > _MAX_CMAP_ENTRIES:
            raise ValueError("CMap mapping entry limit exceeded")

    try:
        for section in re.findall(r"\bbegincodespacerange\b(.*?)\bendcodespacerange\b", text, flags=re.DOTALL):
            values = re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section)
            for start_hex, end_hex in values:
                if len(start_hex) % 2 or len(start_hex) != len(end_hex):
                    raise ValueError("CMap code-space range has inconsistent code lengths")
                start, end = bytes.fromhex(start_hex), bytes.fromhex(end_hex)
                if not start or len(start) > 4 or end < start:
                    raise ValueError("CMap code-space range is invalid")
                code_space_ranges.add((start, end))

        for section in re.findall(r"\bbeginbfchar\b(.*?)\bendbfchar\b", text, flags=re.DOTALL):
            for source, target in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section):
                add(source, target)

        for section in re.findall(r"\bbeginbfrange\b(.*?)\bendbfrange\b", text, flags=re.DOTALL):
            for match in re.finditer(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)",
                section,
                flags=re.DOTALL,
            ):
                start_hex, end_hex, destination = match.groups()
                start, end = int(start_hex, 16), int(end_hex, 16)
                if end < start or end - start > _MAX_CMAP_ENTRIES:
                    raise ValueError("CMap range is invalid or too large")
                if destination.lstrip().startswith("["):
                    values = re.findall(r"<([0-9A-Fa-f]+)>", destination)
                    if len(values) < end - start + 1:
                        raise ValueError("CMap destination array is truncated")
                    for offset, target in enumerate(values[: end - start + 1]):
                        add(f"{start + offset:0{len(start_hex)}X}", target)
                else:
                    target_match = re.search(r"<([0-9A-Fa-f]+)>", destination)
                    if target_match is None:
                        continue
                    target = int(target_match.group(1), 16)
                    target_width = len(target_match.group(1))
                    for offset in range(end - start + 1):
                        value = target + offset
                        if value >= 1 << (target_width * 4):
                            raise ValueError("CMap Unicode range overflows its destination width")
                        add(f"{start + offset:0{len(start_hex)}X}", f"{value:0{target_width}X}")
    except (ValueError, UnicodeDecodeError) as exc:
        return _CMap(mappings, tuple(sorted(code_space_ranges)), tuple(sorted(conflicts)), f"partial:{type(exc).__name__}")
    status = "parsed" if mappings or code_space_ranges else "present_unparsed"
    return _CMap(mappings, tuple(sorted(code_space_ranges)), tuple(sorted(conflicts)), status)


def _parse_cid_encoding_cmap(encoding) -> dict[bytes, int]:
    if not isinstance(encoding, Mapping):
        return {}
    try:
        data = encoding.get_data()
    except Exception:  # noqa: BLE001 - malformed encoding CMaps cannot be safely interpreted.
        return {}
    text = data.decode("latin1", errors="replace")
    output = {}
    for section in re.findall(r"\bbegincidchar\b(.*?)\bendcidchar\b", text, flags=re.DOTALL):
        for source, cid in re.findall(r"<([0-9A-Fa-f]+)>\s+(\d+)", section):
            if len(source) % 2 == 0:
                output[bytes.fromhex(source)] = int(cid)
    for section in re.findall(r"\bbegincidrange\b(.*?)\bendcidrange\b", text, flags=re.DOTALL):
        for source, end, first_cid in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\d+)", section):
            start, stop, base = int(source, 16), int(end, 16), int(first_cid)
            if stop < start or stop - start > _MAX_CMAP_ENTRIES:
                continue
            for offset in range(stop - start + 1):
                output[(start + offset).to_bytes(len(source) // 2, "big")] = base + offset
    return output


def _in_codespace(code: bytes, cmap: _CMap, *, identity: bool) -> bool:
    if cmap.code_space_ranges:
        return any(len(code) == len(start) == len(end) and start <= code <= end for start, end in cmap.code_space_ranges)
    if identity:
        return len(code) == 2
    # With no code-space declaration, accept only an explicit ToUnicode source
    # code. A byte-length match alone is not enough to infer a CID boundary.
    return code in cmap.mappings


def _descriptor_fields(descriptor) -> list[tuple[str, str | int | float | None]]:
    if not isinstance(descriptor, Mapping):
        return []
    keys = ("/Flags", "/FontName", "/FontBBox", "/Ascent", "/Descent", "/ItalicAngle", "/StemV", "/XHeight", "/CapHeight")
    output = []
    for key in keys:
        value = descriptor.get(key)
        if value is None:
            continue
        if hasattr(value, "get_object"):
            try:
                value = value.get_object()
            except Exception:  # noqa: BLE001 - unresolved PDF references are kept explicitly unavailable.
                output.append((key, "unavailable"))
                continue
        if isinstance(value, (list, tuple)):
            stable = [float(item) if isinstance(item, (int, float)) else str(item) for item in value]
            output.append((key, json.dumps(stable, separators=(",", ":"))))
        elif isinstance(value, (str, int, float)):
            output.append((key, value))
        else:
            output.append((key, str(value)))
    return output


def _pdf_name(value) -> str | None:
    if value is None:
        return None
    return str(value) if str(value).startswith("/") else None


__all__ = [
    "PdfFontMetadata",
    "PdfGlyphCodeUse",
    "PdfGlyphInspection",
    "PdfGlyphInspector",
    "glyph_name_to_unicode",
    "normalized_font_name",
]
