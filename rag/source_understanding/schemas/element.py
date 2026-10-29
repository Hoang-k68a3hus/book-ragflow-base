from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .context import (
    Confidence,
    FiniteFloat,
    Identifier,
    JsonObject,
    NormalizedCoordinate,
    SchemaModel,
    StructureSource,
)


class ElementType(StrEnum):
    TITLE = "TITLE"
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    SENTENCE = "SENTENCE"
    LINE = "LINE"
    LIST = "LIST"
    LIST_ITEM = "LIST_ITEM"
    TABLE = "TABLE"
    TABLE_ROW = "TABLE_ROW"
    TABLE_CELL = "TABLE_CELL"
    CODE = "CODE"
    FORMULA = "FORMULA"
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    DIALOGUE_TURN = "DIALOGUE_TURN"
    LOG_ENTRY = "LOG_ENTRY"
    KEY_VALUE = "KEY_VALUE"
    FIGURE = "FIGURE"
    CHART = "CHART"
    CAPTION = "CAPTION"
    FOOTNOTE = "FOOTNOTE"
    SEPARATOR = "SEPARATOR"
    HEADER = "HEADER"
    FOOTER = "FOOTER"
    PAGE_NUMBER = "PAGE_NUMBER"
    UNKNOWN = "UNKNOWN"


class BoundingBox(SchemaModel):
    """Canonical page-relative box using top-left origin and [0, 1] coordinates."""

    x0: NormalizedCoordinate
    y0: NormalizedCoordinate
    x1: NormalizedCoordinate
    y1: NormalizedCoordinate

    @model_validator(mode="after")
    def validate_extents(self) -> BoundingBox:
        if self.x1 < self.x0:
            raise ValueError("bbox x1 must be >= x0")
        if self.y1 < self.y0:
            raise ValueError("bbox y1 must be >= y0")
        return self


class SourceLocation(SchemaModel):
    """Canonical source location with optional dedicated provenance.

    ``page`` is 1-based and only valid for a stable fixed/rendered page view.
    ``bbox`` is normalized to that page with top-left origin. Character offsets
    use the adapter source-text view before canonical normalization, are 0-based,
    and follow the half-open interval ``[start_char, end_char)``. Line ranges are
    1-based and inclusive.

    ``source`` records how the *location itself* was obtained. It is deliberately
    separate from Element provenance because text/type extraction and location
    extraction may have different provenance. Legacy/unknown location provenance
    may remain ``None``; downstream citation projection must then keep only the
    exact element identity rather than invent a provenance class.
    """

    source: StructureSource | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_ranges(self) -> SourceLocation:
        has_location = any(
            value is not None
            for value in (
                self.page,
                self.bbox,
                self.start_char,
                self.end_char,
                self.line_start,
                self.line_end,
            )
        )
        if self.source is not None and not has_location:
            raise ValueError("location source requires source location data")
        if self.bbox is not None and self.page is None:
            raise ValueError("bbox requires page")
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("start_char and end_char must be provided together")
        if self.start_char is not None and self.end_char is not None and self.end_char < self.start_char:
            raise ValueError("end_char must be >= start_char")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be provided together")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class StyleInfo(SchemaModel):
    font_name: str | None = None
    font_size: FiniteFloat | None = Field(default=None, gt=0)
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None
    alignment: str | None = None
    indentation: FiniteFloat | None = None
    attributes: JsonObject = Field(default_factory=dict)


class ElementConfidence(SchemaModel):
    """Measured extraction confidence; ``None`` means not assessed."""

    overall: Confidence | None = None
    text: Confidence | None = None
    type: Confidence | None = None
    order: Confidence | None = None
    location: Confidence | None = None


class TransformationRecord(SchemaModel):
    operation: str = Field(min_length=1, max_length=128)
    before: str | None = None
    after: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class Provenance(SchemaModel):
    source: StructureSource
    extractor: str = Field(min_length=1, max_length=256)
    extractor_version: str | None = Field(default=None, max_length=128)
    confidence: Confidence | None = None
    transformations: tuple[TransformationRecord, ...] = Field(default_factory=tuple)
    metadata: JsonObject = Field(default_factory=dict)


class RawElement(SchemaModel):
    """Adapter-facing element before canonical normalization."""

    text: str | None = None
    type_hint: str | None = Field(default=None, max_length=128)
    order: int = Field(ge=0)
    location: SourceLocation | None = None
    style: StyleInfo | None = None
    attributes: JsonObject = Field(default_factory=dict)
    provenance: Provenance


class Element(SchemaModel):
    id: Identifier
    type: ElementType = ElementType.UNKNOWN
    source_type_hint: str | None = Field(default=None, max_length=128)
    order: int = Field(ge=0)
    raw_text: str | None = None
    normalized_text: str | None = None
    location: SourceLocation | None = None
    style: StyleInfo | None = None
    attributes: JsonObject = Field(default_factory=dict)
    confidence: ElementConfidence = Field(default_factory=ElementConfidence)
    provenance: Provenance
    exclude_from_retrieval: bool = False

    @property
    def text(self) -> str | None:
        return self.normalized_text if self.normalized_text is not None else self.raw_text
