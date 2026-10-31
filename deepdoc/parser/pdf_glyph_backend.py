"""Optional, page-selective PDFium text verification with character geometry.

PDFium is an independent extraction engine, not ground truth. This adapter only
reports the text/font/box it observes; callers must combine it with source-font
metadata before deciding whether any recovery is justified.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader


@dataclass(frozen=True, slots=True)
class PdfGlyphPageGeometry:
    page: int
    width: float
    height: float
    crop_box: tuple[float, float, float, float]
    rotation: int


@dataclass(frozen=True, slots=True)
class PdfiumCharacter:
    page: int
    index: int
    text: str
    bbox: tuple[float, float, float, float] | None
    font_name: str | None
    geometry_status: str


class PdfiumUnavailableError(RuntimeError):
    """The optional PDFium package or native library is unavailable."""


class PdfiumTextBackend:
    """Read text characters and font names for explicitly selected PDF pages."""

    name = "pypdfium2"

    def extract_pages(self, data: bytes, pages: tuple[int, ...] | list[int]) -> dict[int, tuple[PdfiumCharacter, ...]]:
        if not isinstance(data, bytes) or not data:
            raise ValueError("PDFium verification requires non-empty source bytes")
        requested = tuple(dict.fromkeys(pages))
        if not requested:
            return {}
        if any(type(page) is not int or page < 1 for page in requested):
            raise ValueError("PDFium page numbers must be 1-based positive integers")

        try:
            import pypdfium2 as pdfium
        except (ImportError, OSError) as exc:
            raise PdfiumUnavailableError(f"pypdfium2 is unavailable: {exc}") from exc

        try:
            reader = PdfReader(BytesIO(data), strict=False)
            page_count = len(reader.pages)
            if any(number > page_count for number in requested):
                raise ValueError("PDFium page selection is outside the source PDF")
            geometry = {number: _page_geometry(reader.pages[number - 1], number) for number in requested}
            document = pdfium.PdfDocument(data)
        except PdfiumUnavailableError:
            raise
        except Exception as exc:
            raise ValueError(f"cannot open PDF for PDFium verification: {exc}") from exc

        result: dict[int, tuple[PdfiumCharacter, ...]] = {}
        try:
            for number in requested:
                page = None
                text_page = None
                try:
                    page = document[number - 1]
                    text_page = page.get_textpage()
                    page_geometry = geometry[number]
                    characters = []
                    for index in range(text_page.count_chars()):
                        try:
                            text = text_page.get_text_range(index, 1, errors="replace")
                        except (IndexError, RuntimeError, ValueError):
                            text = ""
                        if not text:
                            continue
                        font_name = _font_name(text_page, index)
                        raw_bbox = _char_box(text_page, index)
                        bbox, geometry_status = _to_display_bbox(raw_bbox, page_geometry)
                        characters.append(PdfiumCharacter(number, index, text, bbox, font_name, geometry_status))
                    result[number] = tuple(characters)
                finally:
                    if text_page is not None:
                        text_page.close()
                    if page is not None:
                        page.close()
        except Exception as exc:
            raise RuntimeError(f"PDFium failed while inspecting selected pages: {exc}") from exc
        finally:
            document.close()
        return result


def _page_geometry(page, page_number: int) -> PdfGlyphPageGeometry:
    crop = page.cropbox
    x0, y0, x1, y1 = (float(value) for value in crop)
    width, height = x1 - x0, y1 - y0
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1, width, height)) or width <= 0 or height <= 0:
        raise ValueError(f"page {page_number} has invalid crop geometry")
    rotation = int(page.rotation or 0) % 360
    if rotation not in (0, 90, 180, 270):
        raise ValueError(f"page {page_number} has unsupported rotation {rotation}")
    display_width, display_height = (height, width) if rotation in (90, 270) else (width, height)
    return PdfGlyphPageGeometry(page_number, display_width, display_height, (x0, y0, x1, y1), rotation)


def _font_name(text_page, index: int) -> str | None:
    text_object = text_page.get_textobj(index)
    if text_object is None:
        return None
    try:
        return text_object.get_font().get_base_name()
    except (RuntimeError, ValueError, OSError):
        return None


def _char_box(text_page, index: int) -> tuple[float, float, float, float] | None:
    try:
        values = tuple(float(value) for value in text_page.get_charbox(index))
    except (RuntimeError, ValueError, TypeError, IndexError):
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    left, bottom, right, top = values
    if right <= left or top <= bottom:
        return None
    return left, bottom, right, top


def _to_display_bbox(
    raw_bbox: tuple[float, float, float, float] | None,
    geometry: PdfGlyphPageGeometry,
) -> tuple[tuple[float, float, float, float] | None, str]:
    if raw_bbox is None:
        return None, "unavailable"
    left, bottom, right, top = raw_bbox
    crop_left, crop_bottom, crop_right, crop_top = geometry.crop_box
    x0, y0, x1, y1 = left - crop_left, bottom - crop_bottom, right - crop_left, top - crop_bottom
    crop_width, crop_height = crop_right - crop_left, crop_top - crop_bottom

    # PDFium text boxes are in unrotated PDF user space. Move them into the
    # visible CropBox, then apply the page's clockwise display rotation.
    if geometry.rotation == 90:
        x0, y0, x1, y1 = y0, crop_width - x1, y1, crop_width - x0
    elif geometry.rotation == 180:
        x0, y0, x1, y1 = crop_width - x1, crop_height - y1, crop_width - x0, crop_height - y0
    elif geometry.rotation == 270:
        x0, y0, x1, y1 = crop_height - y1, x0, crop_height - y0, x1

    display_height = geometry.height
    normalized = (x0 / geometry.width, (display_height - y1) / display_height, x1 / geometry.width, (display_height - y0) / display_height)
    if not all(math.isfinite(value) for value in normalized):
        return None, "invalid"
    if normalized[2] <= normalized[0] or normalized[3] <= normalized[1]:
        return None, "invalid"
    if normalized[0] < -0.02 or normalized[1] < -0.02 or normalized[2] > 1.02 or normalized[3] > 1.02:
        return None, "outside_visible_page"
    return tuple(max(0.0, min(1.0, value)) for value in normalized), "normalized_display_frame"


__all__ = [
    "PdfGlyphPageGeometry",
    "PdfiumCharacter",
    "PdfiumTextBackend",
    "PdfiumUnavailableError",
]
