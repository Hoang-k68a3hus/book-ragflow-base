"""Auditable, PDF-specific corrections for demonstrably broken native glyph mappings.

No heuristic replacement is performed here: ASCII text such as "6=" can be valid.
A correction is applied only when its SHA-256, page, Poppler block index, and
entire original block text all match. Evidence must be supplied by the caller.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any


def prepare_verified_glyph_corrections(
    pdf_data: bytes, corrections: Sequence[Mapping[str, Any]] | None
) -> dict[tuple[int, int], dict[str, str]]:
    """Validate human-reviewed corrections before touching any PDF observation.

    Coordinates are block indexes from Poppler -bbox-layout (zero-based within
    the page), not visual bounding boxes that may drift between renderers.
    """
    if corrections is None:
        return {}
    if isinstance(corrections, (str, bytes, Mapping)):
        raise ValueError("verified glyph corrections must be a list of records")

    digest = hashlib.sha256(pdf_data).hexdigest()
    indexed: dict[tuple[int, int], dict[str, str]] = {}
    for index, correction in enumerate(corrections):
        if not isinstance(correction, Mapping):
            raise ValueError(f"glyph correction {index} must be a mapping")
        if correction.get("pdf_sha256") != digest:
            raise ValueError(f"glyph correction {index} does not match the PDF SHA-256")
        page = correction.get("page")
        block = correction.get("block")
        if type(page) is not int or page < 1 or type(block) is not int or block < 0:
            raise ValueError(f"glyph correction {index} needs a 1-based page and zero-based block")
        original = correction.get("original_text")
        corrected = correction.get("corrected_text")
        evidence = correction.get("evidence")
        if not all(isinstance(v, str) and v.strip() for v in (original, corrected, evidence)):
            raise ValueError(f"glyph correction {index} requires original_text, corrected_text, and evidence")
        if original == corrected:
            raise ValueError(f"glyph correction {index} does not change its text")
        key = (page, block)
        if key in indexed:
            raise ValueError(f"duplicate glyph correction for page {page}, block {block}")
        indexed[key] = {
            "original_text": original,
            "corrected_text": corrected,
            "evidence": evidence,
        }
    return indexed


def apply_verified_glyph_correction(
    original_text: str,
    *,
    page: int,
    block: int,
    corrections: Mapping[tuple[int, int], Mapping[str, str]],
) -> tuple[str, dict[str, str] | None]:
    """Return corrected text and immutable-friendly provenance, or untouched text."""
    item = corrections.get((page, block))
    if item is None:
        return original_text, None
    if original_text != item["original_text"]:
        raise ValueError(
            f"Poppler text changed at page {page}, block {block}; "
            "the verified glyph correction must be reviewed again"
        )
    return item["corrected_text"], {
        "original_text": original_text,
        "evidence": item["evidence"],
        "method": "exact_verified_block_override",
    }
