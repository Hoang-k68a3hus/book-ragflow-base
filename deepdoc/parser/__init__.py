"""DeepDOC parser exports.

Parser implementations are loaded on first access. Typed source observations
stay dependency-light so adapters and schema tests do not initialize every
document parser and its optional runtime dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .source_observation import (
    DeepDocBoundingBox,
    DeepDocDiagnostic,
    DeepDocDiagnosticLevel,
    DeepDocExtractionSource,
    DeepDocObservation,
    DeepDocObservationKind,
    DeepDocOutlineItem,
    DeepDocParseResult,
    DeepDocSourceLocation,
    DeepDocStyle,
)

_LAZY_EXPORTS = {
    "DocxParser": (".docx_parser", "RAGFlowDocxParser"),
    "EpubParser": (".epub_parser", "RAGFlowEpubParser"),
    "ExcelParser": (".excel_parser", "RAGFlowExcelParser"),
    "HtmlParser": (".html_parser", "RAGFlowHtmlParser"),
    "JsonParser": (".json_parser", "RAGFlowJsonParser"),
    "MarkdownElementExtractor": (".markdown_parser", "MarkdownElementExtractor"),
    "MarkdownParser": (".markdown_parser", "RAGFlowMarkdownParser"),
    "PdfParser": (".pdf_parser", "RAGFlowPdfParser"),
    "PdfTextParser": (".pdf_text_parser", "PdfTextParser"),
    "PlainParser": (".pdf_parser", "PlainParser"),
    "PptParser": (".ppt_parser", "RAGFlowPptParser"),
    "TxtParser": (".txt_parser", "RAGFlowTxtParser"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_LAZY_EXPORTS))


__all__ = [
    "DeepDocBoundingBox",
    "DeepDocDiagnostic",
    "DeepDocDiagnosticLevel",
    "DeepDocExtractionSource",
    "DeepDocObservation",
    "DeepDocObservationKind",
    "DeepDocOutlineItem",
    "DeepDocParseResult",
    "DeepDocSourceLocation",
    "DeepDocStyle",
    "DocxParser",
    "EpubParser",
    "ExcelParser",
    "HtmlParser",
    "JsonParser",
    "MarkdownElementExtractor",
    "MarkdownParser",
    "PdfParser",
    "PdfTextParser",
    "PlainParser",
    "PptParser",
    "TxtParser",
]
