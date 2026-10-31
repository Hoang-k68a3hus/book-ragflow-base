"""Native PDF text and geometry extraction through Poppler; never invokes OCR."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import replace
from html.parser import HTMLParser

from .pdf_glyph_backend import PdfiumTextBackend
from .pdf_glyph_inspector import (
    GlyphTextBlock,
    PdfGlyphInspector,
    detect_text_anomalies,
)
from .pdf_glyph_repair import (
    apply_verified_font_glyph_correction,
    apply_verified_glyph_correction,
    prepare_verified_glyph_corrections,
)
from .source_observation import (
    DeepDocBoundingBox,
    DeepDocDiagnostic,
    DeepDocDiagnosticLevel,
    DeepDocExtractionSource,
    DeepDocObservation,
    DeepDocObservationKind,
    DeepDocParseResult,
    DeepDocSourceLocation,
)

_HEADING = re.compile(
    r"^(?:(?:chương|chapter)\s+(?:\d+|[IVXLCDM]+)\b|\d+(?:\.\d+){1,4}\.?\s+[^\W\d_]|"
    r"(?:mục lục|contents|table of contents|mở đầu|lời nói đầu|preface|introduction|conclusion)$)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"^\d+$")
_LEADERS = re.compile(r"(?:\.\s*){5,}")


class PdfTextParser:
    """Emit the same typed observations as the visual PDF parser from native text."""

    def extract_source(self, data: bytes, *, glyph_corrections=None) -> DeepDocParseResult:
        if not isinstance(data, bytes) or not data:
            raise ValueError("native PDF text extraction requires non-empty source bytes")
        executable = os.environ.get("PDFTOTEXT_EXECUTABLE") or shutil.which("pdftotext")
        if not executable:
            raise RuntimeError("Plain Text canonical ingestion requires Poppler pdftotext; install poppler-utils or set PDFTOTEXT_EXECUTABLE")
        try:
            version_result = subprocess.run([executable, "-v"], capture_output=True, timeout=10, check=True)
            version_output = (version_result.stderr + version_result.stdout).decode("utf-8", errors="replace")
            version_match = re.search(r"pdftotext version\s+(\S+)", version_output)
            if version_match is None:
                raise RuntimeError("cannot determine the pdftotext version")
            result = subprocess.run(
                [executable, "-bbox-layout", "-enc", "UTF-8", "-", "-"],
                input=data,
                capture_output=True,
                timeout=300,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", errors="replace")[:1000] if exc.stderr else str(exc)
            raise ValueError(f"native PDF text extraction failed: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("native PDF text extraction timed out") from exc
        except OSError as exc:
            raise RuntimeError(f"cannot run pdftotext: {exc}") from exc

        correction_index = prepare_verified_glyph_corrections(data, glyph_corrections)
        applied_corrections: set[tuple[int, int]] = set()
        layout = _PdfLayoutParser()
        layout.feed(result.stdout.decode("utf-8"))
        layout.close()
        if len(layout.stack) != 1:
            raise ValueError("pdftotext returned incomplete layout markup")
        pages = layout.root.findall(".//page")
        if not pages:
            raise ValueError("pdftotext returned no PDF pages")

        glyph_blocks: list[GlyphTextBlock] = []
        for page_number, page in enumerate(pages, start=1):
            width, height = float(page.attrib["width"]), float(page.attrib["height"])
            for block_number, block in enumerate(page.findall(".//block")):
                line_nodes = block.findall("line")
                lines = tuple(" ".join("".join(word.itertext()) for word in line.findall("word")) for line in line_nodes)
                text = "\n".join(lines)
                if not text.strip():
                    continue
                bbox = tuple(float(block.attrib[key]) for key in ("xmin", "ymin", "xmax", "ymax"))
                line_boxes = tuple(
                    tuple(float(line.attrib[key]) for key in ("xmin", "ymin", "xmax", "ymax")) if all(key in line.attrib for key in ("xmin", "ymin", "xmax", "ymax")) else None for line in line_nodes
                )
                glyph_blocks.append(
                    GlyphTextBlock(
                        page=page_number,
                        block=block_number,
                        source_id=f"pdf:text:{page_number}:{block_number}",
                        text=text,
                        page_width=width,
                        page_height=height,
                        lines=lines,
                        line_boxes=line_boxes,
                        bbox=bbox,
                    )
                )

        text_findings = detect_text_anomalies(glyph_blocks)
        suspicious_pages = {finding["page"] for finding in text_findings if finding.get("secondary_verification_recommended")}
        # ``6=`` is the concrete corruption signature reproduced by the
        # repository's real PDF. It is only a candidate shape: it is common
        # valid text and is not itself reported as an error. Inspect source
        # font mappings on those pages to distinguish a font overlay from a
        # literal digit and equals sign.
        candidate_pages = suspicious_pages | {block.page for block in glyph_blocks if re.search(r"\b6\s*=", block.text)}
        inspector = PdfGlyphInspector()
        inspection = inspector.inspect(data, suspicious_pages=suspicious_pages, pages_to_inspect=candidate_pages)
        backend_pages = {}
        backend_error = None
        if inspection.pages_to_verify:
            try:
                backend_pages = PdfiumTextBackend().extract_pages(data, inspection.pages_to_verify)
            except Exception as exc:  # noqa: BLE001 - secondary PDF backend failures preserve source text.
                backend_error = f"{type(exc).__name__}: {exc}"[:1000]
        analysis = inspector.verify(inspection, backend_pages, glyph_blocks)
        automatic_corrections: dict[tuple[int, int], object] = {}
        ambiguous_auto_keys: set[tuple[int, int]] = set()
        for correction in analysis.corrections:
            key = (correction.page, correction.block)
            if key in automatic_corrections:
                ambiguous_auto_keys.add(key)
            else:
                automatic_corrections[key] = correction
        for key in ambiguous_auto_keys:
            automatic_corrections.pop(key, None)

        observations: list[DeepDocObservation] = []
        diagnostics: list[DeepDocDiagnostic] = [_glyph_diagnostic(finding) for finding in (*inspection.diagnostics, *text_findings, *analysis.findings)]
        if backend_error:
            diagnostics.append(
                DeepDocDiagnostic(
                    code="PDF_GLYPH_SECONDARY_BACKEND_FAILURE",
                    message="PDFium could not inspect one or more suspected pages; native text was preserved",
                    level=DeepDocDiagnosticLevel.WARNING,
                    metadata={"pages": list(inspection.pages_to_verify), "reason": backend_error},
                )
            )
        for page_number, block_number in sorted(ambiguous_auto_keys):
            diagnostics.append(
                DeepDocDiagnostic(
                    code="PDF_FONT_GLYPH_RECOVERY_CONFLICT",
                    message=f"Multiple automatic glyph proposals target page {page_number}, block {block_number}; text was preserved",
                    level=DeepDocDiagnosticLevel.WARNING,
                    metadata={"page": page_number, "block": block_number, "status": "CONFLICT"},
                )
            )
        headers: dict[str, set[int]] = defaultdict(set)
        header_candidates: dict[int, str] = {}
        text_pages = 0
        for page_number, page in enumerate(pages, start=1):
            width, height = float(page.attrib["width"]), float(page.attrib["height"])
            page_words = " ".join("".join(word.itertext()) for word in page.findall(".//word"))
            navigation_page = len(_LEADERS.findall(page_words)) >= 3
            count_before = len(observations)
            for block_number, block in enumerate(page.findall(".//block")):
                lines = [" ".join("".join(word.itertext()) for word in line.findall("word")) for line in block.findall("line")]
                text = "\n".join(lines)
                if not text.strip():
                    continue
                text, glyph_provenance = apply_verified_glyph_correction(text, page=page_number, block=block_number, corrections=correction_index)
                if glyph_provenance is None and (page_number, block_number) in automatic_corrections:
                    text, glyph_provenance = apply_verified_font_glyph_correction(
                        text,
                        pdf_sha256=inspection.pdf_sha256,
                        page=page_number,
                        block=block_number,
                        correction=automatic_corrections[(page_number, block_number)],
                    )
                if glyph_provenance is not None:
                    applied_corrections.add((page_number, block_number))
                    method = glyph_provenance.get("method", "exact_verified_block_override")
                    diagnostics.append(
                        DeepDocDiagnostic(
                            code="PDF_VERIFIED_GLYPH_CORRECTION" if method == "exact_verified_block_override" else "PDF_FONT_GLYPH_RECOVERED",
                            message=f"Verified glyph correction on page {page_number}, block {block_number}",
                            level=DeepDocDiagnosticLevel.INFO,
                            metadata={"page": page_number, "block": block_number, "provenance": glyph_provenance},
                        )
                    )
                bbox = DeepDocBoundingBox(*(float(block.attrib[key]) for key in ("xmin", "ymin", "xmax", "ymax")))
                short_text = " ".join(text.split())
                heading = len(short_text) <= 180 and len(lines) <= 3 and _HEADING.match(short_text) is not None
                page_number_block = _NUMBER.fullmatch(short_text) is not None and (bbox.y1 < height * 0.1 or bbox.y0 > height * 0.9)
                type_hint = "PAGE_NUMBER" if page_number_block else "HEADING" if heading else "PARAGRAPH"
                if page_number > 1 and bbox.y1 < height * 0.15 and len(short_text) <= 180 and not page_number_block:
                    signature = re.sub(r"^\d+\s+|\s+\d+$", "", short_text)
                    headers[signature].add(page_number)
                    header_candidates[len(observations)] = signature
                observations.append(
                    DeepDocObservation(
                        source_id=f"pdf:text:{page_number}:{block_number}",
                        order=len(observations),
                        kind=DeepDocObservationKind.TEXT,
                        text=text,
                        extraction_source=DeepDocExtractionSource.DERIVED if heading or glyph_provenance else DeepDocExtractionSource.NATIVE,
                        type_hint=type_hint,
                        locations=(DeepDocSourceLocation(page=page_number, page_width=width, page_height=height, bbox=bbox),),
                        attributes={
                            "text_extraction_source": "NATIVE",
                            "ocr_used": False,
                            "heading_rule_applied": heading,
                            "source_zone": "navigation" if navigation_page else "body",
                            **({"glyph_provenance": glyph_provenance} if glyph_provenance else {}),
                        },
                    )
                )
            if len(observations) > count_before:
                text_pages += 1
            else:
                diagnostics.append(
                    DeepDocDiagnostic(
                        code="PDF_PAGE_WITHOUT_NATIVE_TEXT",
                        message=f"PDF page {page_number} has no native text; OCR was not attempted",
                        affects_structural_completeness=True,
                        metadata={"page": page_number},
                    )
                )
        unmatched = correction_index.keys() - applied_corrections
        if unmatched:
            raise ValueError(f"glyph corrections did not match Poppler blocks: {sorted(unmatched)}")
        if not observations:
            raise ValueError("PDF has no extractable native text; Plain Text ingestion never falls back to OCR")
        for index, signature in header_candidates.items():
            if len(headers[signature]) >= 3:
                observation = observations[index]
                observations[index] = replace(observation, type_hint="HEADER", extraction_source=DeepDocExtractionSource.DERIVED, attributes={**observation.attributes, "source_zone": "header"})
        unresolved_glyph_findings = (
            bool(text_findings)
            or any(finding.get("status") in {"SUSPECT", "CONFLICT"} for finding in analysis.findings)
            or any(finding.get("code") != "PDF_FONT_GLYPH_MAPPING_SUSPECT" for finding in inspection.diagnostics)
        )
        return DeepDocParseResult(
            parser_name="poppler-pdftotext",
            parser_version=version_match.group(1),
            media_type="application/pdf",
            observations=tuple(observations),
            diagnostics=tuple(diagnostics),
            attributes={
                "extraction_backend": "native_text",
                "ocr_used": False,
                "verified_glyph_correction_count": len(applied_corrections),
                "manual_glyph_correction_count": sum(1 for diagnostic in diagnostics if diagnostic.code == "PDF_VERIFIED_GLYPH_CORRECTION"),
                "font_glyph_recovery_count": sum(1 for diagnostic in diagnostics if diagnostic.code == "PDF_FONT_GLYPH_RECOVERED"),
                "pdf_glyph_inspection": {
                    **inspection.summary(),
                    "text_anomaly_count": len(text_findings),
                    "verification_finding_count": len(analysis.findings),
                    "status": ("SUSPECT" if unresolved_glyph_findings else "VERIFIED" if any(diagnostic.code == "PDF_FONT_GLYPH_RECOVERED" for diagnostic in diagnostics) else "CLEAN"),
                    "candidate_page_count": len(candidate_pages),
                    "secondary_backend_calls": len(backend_pages),
                },
                "page_count": len(pages),
                "text_page_count": text_pages,
                "block_order": "poppler_bbox_layout",
                "heading_policy_version": "1",
                "navigation_page_count": len({observation.locations[0].page for observation in observations if observation.attributes["source_zone"] == "navigation"}),
            },
        )


def _glyph_diagnostic(finding: dict) -> DeepDocDiagnostic:
    status = finding.get("status", "SUSPECT")
    level = DeepDocDiagnosticLevel.INFO if status in {"CLEAN", "VERIFIED"} else DeepDocDiagnosticLevel.WARNING
    reason = finding.get("reason") or finding.get("code") or "PDF glyph inspection finding"
    code = str(finding.get("code") or "PDF_GLYPH_INSPECTION")[:128]
    return DeepDocDiagnostic(
        code=code,
        message=str(reason)[:4096],
        level=level,
        metadata=dict(finding),
    )


class _PdfLayoutParser(HTMLParser):
    """Read Poppler XHTML while preserving PDF control characters in word text."""

    _tags = frozenset({"doc", "page", "flow", "block", "line", "word"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("layout")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._tags:
            element = ET.SubElement(self.stack[-1], tag, {key: value for key, value in attrs if value is not None})
            self.stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._tags:
            if len(self.stack) == 1 or self.stack[-1].tag != tag:
                raise ValueError("pdftotext returned inconsistent layout markup")
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.stack[-1].tag == "word":
            self.stack[-1].text = (self.stack[-1].text or "") + data
