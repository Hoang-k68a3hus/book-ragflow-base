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

from .source_observation import (
    DeepDocBoundingBox,
    DeepDocDiagnostic,
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

    def extract_source(self, data: bytes) -> DeepDocParseResult:
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

        layout = _PdfLayoutParser()
        layout.feed(result.stdout.decode("utf-8"))
        layout.close()
        if len(layout.stack) != 1:
            raise ValueError("pdftotext returned incomplete layout markup")
        pages = layout.root.findall(".//page")
        if not pages:
            raise ValueError("pdftotext returned no PDF pages")
        observations: list[DeepDocObservation] = []
        diagnostics: list[DeepDocDiagnostic] = []
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
                        extraction_source=DeepDocExtractionSource.DERIVED if heading else DeepDocExtractionSource.NATIVE,
                        type_hint=type_hint,
                        locations=(DeepDocSourceLocation(page=page_number, page_width=width, page_height=height, bbox=bbox),),
                        attributes={"text_extraction_source": "NATIVE", "ocr_used": False, "heading_rule_applied": heading, "source_zone": "navigation" if navigation_page else "body"},
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
        if not observations:
            raise ValueError("PDF has no extractable native text; Plain Text ingestion never falls back to OCR")
        for index, signature in header_candidates.items():
            if len(headers[signature]) >= 3:
                observation = observations[index]
                observations[index] = replace(observation, type_hint="HEADER", extraction_source=DeepDocExtractionSource.DERIVED, attributes={**observation.attributes, "source_zone": "header"})
        return DeepDocParseResult(
            parser_name="poppler-pdftotext",
            parser_version=version_match.group(1),
            media_type="application/pdf",
            observations=tuple(observations),
            diagnostics=tuple(diagnostics),
            attributes={
                "extraction_backend": "native_text",
                "ocr_used": False,
                "page_count": len(pages),
                "text_page_count": text_pages,
                "block_order": "poppler_bbox_layout",
                "heading_policy_version": "1",
                "navigation_page_count": len({observation.locations[0].page for observation in observations if observation.attributes["source_zone"] == "navigation"}),
            },
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
