from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import Field, field_validator, model_validator

from .context import (
    Confidence,
    ContentHash,
    ContextNodeRef,
    Identifier,
    JsonObject,
    SchemaModel,
    StructureSource,
)
from .element import BoundingBox

if TYPE_CHECKING:
    from .document import CanonicalDocument


class RetrievalUnitType(StrEnum):
    TEXT = "TEXT"
    SECTION = "SECTION"
    QA_PAIR = "QA_PAIR"
    DIALOGUE = "DIALOGUE"
    PROCEDURE = "PROCEDURE"
    DEFINITION = "DEFINITION"
    EXAMPLE = "EXAMPLE"
    EXERCISE = "EXERCISE"
    CODE = "CODE"
    TABLE = "TABLE"
    LOG = "LOG"
    LIST = "LIST"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class AnnotationRef(SchemaModel):
    id: Identifier
    type: str | None = Field(default=None, max_length=128)
    value: str | None = Field(default=None, max_length=2048)
    source: StructureSource | None = None
    confidence: Confidence | None = None


class SourceAnchor(SchemaModel):
    source_id: Identifier
    content_hash: ContentHash
    source_revision: Identifier | None = None
    element_id: Identifier
    location_source: StructureSource | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: BoundingBox | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_location(self) -> SourceAnchor:
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
        if has_location and self.location_source is None:
            raise ValueError("source anchor location requires location_source")
        if not has_location and self.location_source is not None:
            raise ValueError("location_source requires source anchor location data")
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

    def identity_key(self) -> tuple[object, ...]:
        bbox_key = None
        if self.bbox is not None:
            bbox_key = (self.bbox.x0, self.bbox.y0, self.bbox.x1, self.bbox.y1)
        return (
            self.source_id,
            self.content_hash,
            self.source_revision,
            self.element_id,
            self.location_source,
            self.page,
            bbox_key,
            self.start_char,
            self.end_char,
            self.line_start,
            self.line_end,
        )


class RetrievalUnit(SchemaModel):
    """Task-facing projection fully traceable to one canonical source revision."""

    id: Identifier
    document_id: Identifier
    content_hash: ContentHash
    source_revision: Identifier | None = None
    subdocument_id: Identifier | None = None
    logical_unit_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    element_ids: tuple[Identifier, ...] = Field(min_length=1)
    retrieval_text: str = Field(min_length=1)
    display_text: str = Field(min_length=1)
    context_path: tuple[ContextNodeRef, ...] = Field(default_factory=tuple)
    semantic_annotations: tuple[AnnotationRef, ...] = Field(default_factory=tuple)
    source_anchors: tuple[SourceAnchor, ...] = Field(min_length=1)
    unit_type: RetrievalUnitType = RetrievalUnitType.TEXT
    token_count: int = Field(ge=1)
    quality: Confidence | None = None
    version: str = Field(min_length=1, max_length=128)
    metadata: JsonObject = Field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return self.document_id

    @field_validator("retrieval_text", "display_text", "version")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace characters")
        return value

    @model_validator(mode="after")
    def validate_refs_and_anchors(self) -> RetrievalUnit:
        for name, values in (
            ("logical_unit_ids", self.logical_unit_ids),
            ("element_ids", self.element_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")

        context_ids = [ref.id for ref in self.context_path]
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("context_path ids must be unique")

        annotation_ids = [ref.id for ref in self.semantic_annotations]
        if len(annotation_ids) != len(set(annotation_ids)):
            raise ValueError("semantic annotation ids must be unique")

        anchor_keys = [anchor.identity_key() for anchor in self.source_anchors]
        if len(anchor_keys) != len(set(anchor_keys)):
            raise ValueError("source_anchors must be unique")

        element_ids = set(self.element_ids)
        covered_elements: set[str] = set()
        for anchor in self.source_anchors:
            if anchor.source_id != self.document_id:
                raise ValueError(f"source anchor {anchor.element_id!r} uses source_id {anchor.source_id!r}, expected {self.document_id!r}")
            if anchor.content_hash != self.content_hash:
                raise ValueError(f"source anchor {anchor.element_id!r} content_hash does not match retrieval unit")
            if anchor.source_revision != self.source_revision:
                raise ValueError(f"source anchor {anchor.element_id!r} source_revision does not match retrieval unit")
            if anchor.element_id not in element_ids:
                raise ValueError(f"source anchor references element {anchor.element_id!r} outside retrieval unit element_ids")
            covered_elements.add(anchor.element_id)

        missing_anchors = element_ids - covered_elements
        if missing_anchors:
            raise ValueError(f"every retrieval element must have at least one source anchor; missing: {sorted(missing_anchors)}")
        return self

    def validate_against_document(self, document: CanonicalDocument) -> RetrievalUnit:
        """Validate references that require the canonical document graph."""

        if self.document_id != document.document_id:
            raise ValueError(f"retrieval unit document_id {self.document_id!r} does not match canonical document {document.document_id!r}")
        if self.content_hash != document.content_hash:
            raise ValueError("retrieval unit content_hash does not match canonical document")
        if self.source_revision != document.source_revision:
            raise ValueError("retrieval unit source_revision does not match canonical document")

        elements = {element.id: element for element in document.elements}
        logical_units = {unit.id: unit for unit in document.logical_units}
        context_nodes = {node.id: node for node in document.context_nodes}
        annotations = {annotation.id: annotation for annotation in document.semantic_annotations}
        subdocuments = {subdoc.id: subdoc for subdoc in document.subdocuments}

        missing_elements = set(self.element_ids) - elements.keys()
        if missing_elements:
            raise ValueError(f"retrieval unit contains unknown element_ids: {sorted(missing_elements)}")
        missing_logical = set(self.logical_unit_ids) - logical_units.keys()
        if missing_logical:
            raise ValueError(f"retrieval unit contains unknown logical_unit_ids: {sorted(missing_logical)}")

        if self.subdocument_id is not None:
            subdocument = subdocuments.get(self.subdocument_id)
            if subdocument is None:
                raise ValueError(f"retrieval unit references unknown subdocument_id {self.subdocument_id!r}")
            if not set(self.element_ids).issubset(subdocument.element_ids):
                raise ValueError(f"retrieval unit contains elements outside subdocument {self.subdocument_id!r}")

        previous_context_id: str | None = None
        for ref in self.context_path:
            node = context_nodes.get(ref.id)
            if node is None:
                raise ValueError(f"retrieval context references unknown node {ref.id!r}")
            if ref.type is not None and ref.type != node.type:
                raise ValueError(f"retrieval context type disagrees for node {ref.id!r}")
            if ref.label is not None and ref.label != node.label:
                raise ValueError(f"retrieval context label disagrees for node {ref.id!r}")
            if ref.source is not None and ref.source != node.source:
                raise ValueError(f"retrieval context source disagrees for node {ref.id!r}")
            if ref.confidence is not None and ref.confidence != node.confidence:
                raise ValueError(f"retrieval context confidence disagrees for node {ref.id!r}")
            if previous_context_id is None:
                if node.parent_id is not None:
                    raise ValueError("context_path must start at a root context node")
            elif node.parent_id != previous_context_id:
                raise ValueError("context_path must follow canonical parent_id links")
            previous_context_id = node.id

        for ref in self.semantic_annotations:
            annotation = annotations.get(ref.id)
            if annotation is None:
                raise ValueError(f"retrieval annotation references unknown id {ref.id!r}")
            if ref.type is not None and ref.type != annotation.type.value:
                raise ValueError(f"retrieval annotation type disagrees for {ref.id!r}")
            if ref.value is not None and ref.value != annotation.value:
                raise ValueError(f"retrieval annotation value disagrees for {ref.id!r}")
            if ref.source is not None and ref.source != annotation.source:
                raise ValueError(f"retrieval annotation source disagrees for {ref.id!r}")
            if ref.confidence is not None and ref.confidence != annotation.confidence:
                raise ValueError(f"retrieval annotation confidence disagrees for {ref.id!r}")

        for anchor in self.source_anchors:
            element = elements[anchor.element_id]
            if anchor.location_source == StructureSource.DERIVED:
                continue
            anchor_has_location = any(
                getattr(anchor, field_name) is not None
                for field_name in (
                    "page",
                    "bbox",
                    "start_char",
                    "end_char",
                    "line_start",
                    "line_end",
                )
            )
            if not anchor_has_location:
                continue
            if element.location is None:
                raise ValueError(f"non-derived anchor for element {element.id!r} has location not present on canonical element")
            if element.location.source is None:
                raise ValueError(f"non-derived anchor for element {element.id!r} cannot claim location provenance absent from canonical location")
            if anchor.location_source != element.location.source:
                raise ValueError(f"non-derived anchor location_source disagrees with element {element.id!r}")
            for field_name in (
                "page",
                "bbox",
                "start_char",
                "end_char",
                "line_start",
                "line_end",
            ):
                anchor_value = getattr(anchor, field_name)
                if anchor_value is None:
                    continue
                if getattr(element.location, field_name) != anchor_value:
                    raise ValueError(f"non-derived anchor {field_name} disagrees with element {element.id!r}")

        return self
