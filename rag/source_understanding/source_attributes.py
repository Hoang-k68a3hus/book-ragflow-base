"""Stable attribute keys shared by source adapters and structural understanding.

These keys describe source-near observations carried in ``RawElement.attributes`` /
``Element.attributes``. They are deliberately not format-specific: DOCX, HTML,
Markdown, PDF-layout adapters and future sources may emit the same facts.
"""

from __future__ import annotations

from rag.source_understanding.schemas.element import Element

SOURCE_ATTRIBUTE_CONTRACT_VERSION = "3"
HEADING_LEVEL_ATTRIBUTE = "heading_level"
INTEGRITY_GROUP_ID_ATTRIBUTE = "integrity_group_id"
INTEGRITY_PARENT_GROUP_ID_ATTRIBUTE = "integrity_parent_group_id"
NUMBERING_FORMAT_ATTRIBUTE = "number_format"
NUMBERING_LEVEL_ATTRIBUTE = "numbering_level"
NUMBERING_SEQUENCE_ID_ATTRIBUTE = "numbering_id"
SOURCE_ANCHOR_ATTRIBUTE = "source_anchor"
SOURCE_REFERENCES_ATTRIBUTE = "source_references"
SOURCE_LABEL_ATTRIBUTE = "source_label"
SOURCE_ZONE_ATTRIBUTE = "source_zone"


class SourceAttributeError(ValueError):
    """A reserved source attribute violates its cross-adapter contract."""


def source_integrity_group_id(element: Element) -> str | None:
    return _identifier_attribute(element, INTEGRITY_GROUP_ID_ATTRIBUTE)


def source_integrity_parent_group_id(element: Element) -> str | None:
    value = _identifier_attribute(element, INTEGRITY_PARENT_GROUP_ID_ATTRIBUTE)
    group_id = source_integrity_group_id(element)
    if value is not None and value == group_id:
        raise SourceAttributeError("native integrity group cannot be its own parent")
    return value


def source_numbering_sequence_id(element: Element) -> str | None:
    """Return a source-native numbering sequence identity when one is available."""

    return _identifier_attribute(element, NUMBERING_SEQUENCE_ID_ATTRIBUTE)


def source_numbering_level(element: Element) -> int | None:
    """Return the zero-based native numbering depth without inferring hierarchy."""

    value = element.attributes.get(NUMBERING_LEVEL_ATTRIBUTE)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceAttributeError(f"{NUMBERING_LEVEL_ATTRIBUTE} for element {element.id!r} must be an integer")
    if value < 0 or value > 64:
        raise SourceAttributeError(f"{NUMBERING_LEVEL_ATTRIBUTE} for element {element.id!r} must be between 0 and 64")
    return value


def source_numbering_format(element: Element) -> str | None:
    """Return the source-native numbering format token when explicitly observed."""

    value = element.attributes.get(NUMBERING_FORMAT_ATTRIBUTE)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 128:
        raise SourceAttributeError(f"{NUMBERING_FORMAT_ATTRIBUTE} for element {element.id!r} must be a trimmed non-blank string <= 128 chars")
    return value


def source_zone(element: Element) -> str | None:
    value = element.attributes.get(SOURCE_ZONE_ATTRIBUTE)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 128:
        raise SourceAttributeError(f"{SOURCE_ZONE_ATTRIBUTE} for element {element.id!r} must be a trimmed non-blank string <= 128 chars")
    return value


def source_heading_level(element: Element) -> int | None:
    value = element.attributes.get(HEADING_LEVEL_ATTRIBUTE)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceAttributeError(f"{HEADING_LEVEL_ATTRIBUTE} for element {element.id!r} must be an integer")
    if value < 1 or value > 64:
        raise SourceAttributeError(f"{HEADING_LEVEL_ATTRIBUTE} for element {element.id!r} must be between 1 and 64")
    return value


def source_label(element: Element) -> str | None:
    """Return an explicit source-provided structural label when available.

    This is useful for markup sources whose exact raw source span contains block
    syntax (for example ``# Heading``) while the source also explicitly defines
    the heading label (``Heading``). It is not a semantic summary.
    """

    value = element.attributes.get(SOURCE_LABEL_ATTRIBUTE)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SourceAttributeError(f"{SOURCE_LABEL_ATTRIBUTE} for element {element.id!r} must be a trimmed non-blank string")
    return value


def source_anchor(element: Element) -> tuple[str, str] | None:
    value = element.attributes.get(SOURCE_ANCHOR_ATTRIBUTE)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SourceAttributeError(f"{SOURCE_ANCHOR_ATTRIBUTE} for element {element.id!r} must be an object")
    return _source_reference_pair(element, value, SOURCE_ANCHOR_ATTRIBUTE)


def source_references(element: Element) -> tuple[tuple[str, str], ...]:
    value = element.attributes.get(SOURCE_REFERENCES_ATTRIBUTE)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SourceAttributeError(f"{SOURCE_REFERENCES_ATTRIBUTE} for element {element.id!r} must be a list")
    output: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise SourceAttributeError(f"{SOURCE_REFERENCES_ATTRIBUTE}[{index}] for element {element.id!r} must be an object")
        output.append(_source_reference_pair(element, item, f"{SOURCE_REFERENCES_ATTRIBUTE}[{index}]"))
    return tuple(output)


def _source_reference_pair(
    element: Element,
    value: dict[str, object],
    field_name: str,
) -> tuple[str, str]:
    kind = value.get("kind")
    reference_id = value.get("id")
    if not isinstance(kind, str) or not kind or kind.strip() != kind or len(kind) > 128:
        raise SourceAttributeError(f"{field_name}.kind for element {element.id!r} must be a trimmed non-blank string <= 128 chars")
    if not isinstance(reference_id, str) or not reference_id or reference_id.strip() != reference_id or len(reference_id) > 256:
        raise SourceAttributeError(f"{field_name}.id for element {element.id!r} must be a trimmed non-blank string <= 256 chars")
    return kind, reference_id


def _identifier_attribute(element: Element, key: str) -> str | None:
    value = element.attributes.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 256:
        raise SourceAttributeError(f"{key} for element {element.id!r} must be a trimmed non-blank string <= 256 chars")
    return value
