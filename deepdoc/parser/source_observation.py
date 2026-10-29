"""Typed, source-near observations emitted by DeepDOC extractors.

The contract in this module intentionally contains no RAG, storage, chunking, or
indexing types.  DeepDOC reports what it observed; downstream packages decide how
to normalize and interpret those observations.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import SupportsFloat

type DeepDocJsonScalar = str | int | float | bool | None
type DeepDocJsonValue = DeepDocJsonScalar | list[DeepDocJsonValue] | tuple[DeepDocJsonValue, ...] | Mapping[str, DeepDocJsonValue]


class DeepDocObservationKind(StrEnum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    FIGURE = "FIGURE"


class DeepDocExtractionSource(StrEnum):
    """How DeepDOC obtained an observation.

    ``DERIVED`` is the conservative value when native-text and OCR contributions
    cannot be distinguished for one merged observation.
    """

    NATIVE = "NATIVE"
    OCR = "OCR"
    DERIVED = "DERIVED"


class DeepDocDiagnosticLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class DeepDocBoundingBox:
    """Page-local absolute coordinates with a top-left origin."""

    x0: int | float
    y0: int | float
    x1: int | float
    y1: int | float

    def __post_init__(self) -> None:
        values = tuple(_finite_number(value, "DeepDOC bounding-box coordinate") for value in (self.x0, self.y0, self.x1, self.y1))
        x0, y0, x1, y1 = values
        if x1 < x0 or y1 < y0:
            raise ValueError("DeepDOC bounding-box extents are invalid")
        object.__setattr__(self, "x0", x0)
        object.__setattr__(self, "y0", y0)
        object.__setattr__(self, "x1", x1)
        object.__setattr__(self, "y1", y1)


@dataclass(frozen=True, slots=True)
class DeepDocSourceLocation:
    page: int
    page_width: int | float
    page_height: int | float
    bbox: DeepDocBoundingBox | None = None

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("DeepDOC source pages are 1-based")
        page_width = _finite_number(self.page_width, "DeepDOC page width")
        page_height = _finite_number(self.page_height, "DeepDOC page height")
        if page_width <= 0 or page_height <= 0:
            raise ValueError("DeepDOC source page dimensions must be finite and positive")
        object.__setattr__(self, "page_width", page_width)
        object.__setattr__(self, "page_height", page_height)


@dataclass(frozen=True, slots=True)
class DeepDocStyle:
    font_name: str | None = None
    font_size: int | float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None
    alignment: str | None = None
    indentation: int | float | None = None
    attributes: Mapping[str, DeepDocJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.font_size is not None:
            font_size = _finite_number(self.font_size, "DeepDOC font size")
            if font_size <= 0:
                raise ValueError("DeepDOC font size must be finite and positive")
            object.__setattr__(self, "font_size", font_size)
        if self.indentation is not None:
            object.__setattr__(self, "indentation", _finite_number(self.indentation, "DeepDOC indentation"))
        object.__setattr__(self, "attributes", _freeze_json_object(self.attributes))


@dataclass(frozen=True, slots=True)
class DeepDocObservation:
    source_id: str
    order: int
    kind: DeepDocObservationKind
    text: str | None
    extraction_source: DeepDocExtractionSource = DeepDocExtractionSource.DERIVED
    type_hint: str | None = None
    layout_type: str | None = None
    locations: tuple[DeepDocSourceLocation, ...] = ()
    style: DeepDocStyle | None = None
    attributes: Mapping[str, DeepDocJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id or self.source_id.strip() != self.source_id:
            raise ValueError("DeepDOC observation source_id must be trimmed and non-blank")
        if len(self.source_id) > 4096:
            raise ValueError("DeepDOC observation source_id must not exceed 4096 characters")
        if self.order < 0:
            raise ValueError("DeepDOC observation order must be non-negative")
        if self.type_hint is not None and self.type_hint.strip() != self.type_hint:
            raise ValueError("DeepDOC observation type_hint must be trimmed")
        if self.type_hint is not None and len(self.type_hint) > 128:
            raise ValueError("DeepDOC observation type_hint must not exceed 128 characters")
        if self.layout_type is not None and self.layout_type.strip() != self.layout_type:
            raise ValueError("DeepDOC observation layout_type must be trimmed")
        object.__setattr__(self, "locations", tuple(self.locations))
        object.__setattr__(self, "attributes", _freeze_json_object(self.attributes))


@dataclass(frozen=True, slots=True)
class DeepDocOutlineItem:
    title: str
    level: int
    page: int | None = None

    def __post_init__(self) -> None:
        if not self.title or self.title.strip() != self.title:
            raise ValueError("DeepDOC outline title must be trimmed and non-blank")
        if self.level < 1:
            raise ValueError("DeepDOC outline levels are 1-based")
        if self.page is not None and self.page < 1:
            raise ValueError("DeepDOC outline pages are 1-based")


@dataclass(frozen=True, slots=True)
class DeepDocDiagnostic:
    code: str
    message: str
    level: DeepDocDiagnosticLevel = DeepDocDiagnosticLevel.WARNING
    affects_structural_completeness: bool = False
    metadata: Mapping[str, DeepDocJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code or self.code.strip() != self.code:
            raise ValueError("DeepDOC diagnostic code must be trimmed and non-blank")
        if len(self.code) > 128:
            raise ValueError("DeepDOC diagnostic code must not exceed 128 characters")
        if not self.message or self.message.strip() != self.message:
            raise ValueError("DeepDOC diagnostic message must be trimmed and non-blank")
        if len(self.message) > 4096:
            raise ValueError("DeepDOC diagnostic message must not exceed 4096 characters")
        object.__setattr__(self, "metadata", _freeze_json_object(self.metadata))


@dataclass(frozen=True, slots=True)
class DeepDocParseResult:
    parser_name: str
    parser_version: str
    media_type: str
    observations: tuple[DeepDocObservation, ...]
    outline: tuple[DeepDocOutlineItem, ...] = ()
    diagnostics: tuple[DeepDocDiagnostic, ...] = ()
    attributes: Mapping[str, DeepDocJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("parser_name", self.parser_name),
            ("parser_version", self.parser_version),
            ("media_type", self.media_type),
        ):
            if not value or value.strip() != value:
                raise ValueError(f"DeepDOC {field_name} must be trimmed and non-blank")
        if len(self.parser_name) > 256:
            raise ValueError("DeepDOC parser_name must not exceed 256 characters")
        if len(self.parser_version) > 128:
            raise ValueError("DeepDOC parser_version must not exceed 128 characters")
        if len(self.media_type) > 256:
            raise ValueError("DeepDOC media_type must not exceed 256 characters")

        observations = tuple(self.observations)
        orders = [item.order for item in observations]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("DeepDOC observations must have unique ascending order values")
        source_ids = [item.source_id for item in observations]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("DeepDOC observations must have unique source ids")

        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "outline", tuple(self.outline))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "attributes", _freeze_json_object(self.attributes))


def _freeze_json_object(
    value: Mapping[str, DeepDocJsonValue],
) -> Mapping[str, DeepDocJsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError("DeepDOC attributes must be a mapping")
    frozen: dict[str, DeepDocJsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("DeepDOC attribute keys must be strings")
        frozen[key] = _freeze_json_value(item)
    return MappingProxyType(frozen)


def _finite_number(value: SupportsFloat, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite")
    return normalized


def _freeze_json_value(value: DeepDocJsonValue) -> DeepDocJsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("DeepDOC attributes cannot contain non-finite floats")
        return value
    if isinstance(value, Mapping):
        return _freeze_json_object(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    raise TypeError(f"DeepDOC attributes contain unsupported value {type(value)!r}")


__all__ = [
    "DeepDocBoundingBox",
    "DeepDocDiagnostic",
    "DeepDocDiagnosticLevel",
    "DeepDocExtractionSource",
    "DeepDocJsonValue",
    "DeepDocObservation",
    "DeepDocObservationKind",
    "DeepDocOutlineItem",
    "DeepDocParseResult",
    "DeepDocSourceLocation",
    "DeepDocStyle",
]
