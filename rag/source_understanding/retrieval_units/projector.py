from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from pydantic import Field, model_validator

from rag.source_understanding.artifacts import ArtifactContentEncoding, CanonicalArtifactReference
from rag.source_understanding.artifacts.serialization import canonical_artifact_hash, serialize_canonical_document
from rag.source_understanding.schemas.context import (
    Confidence,
    ContentHash,
    Identifier,
    SchemaModel,
    StructureSource,
)
from rag.source_understanding.schemas.document import CanonicalDocument
from rag.source_understanding.schemas.retrieval_unit import (
    RetrievalUnit,
    RetrievalUnitType,
    SourceAnchor,
)

RETRIEVAL_UNIT_INDEX_PROJECTOR_VERSION = "4"


class RetrievalQualityPolicy(SchemaModel):
    minimum_multiplier: Confidence = 0.75
    anomaly_penalty_per_occurrence: Confidence = 0.01


class RetrievalUnitProjectionError(ValueError):
    """Retrieval units cannot be safely projected into rebuildable index rows."""


class ProjectedContextNode(SchemaModel):
    id: Identifier
    type: str | None = Field(default=None, max_length=2048)
    label: str | None = Field(default=None, max_length=2048)
    source: StructureSource | None = None
    confidence: Confidence | None = None


class ProjectedSourceAnchor(SchemaModel):
    element_id: Identifier
    location_source: StructureSource | None = None
    page: int | None = Field(default=None, ge=1)
    x0: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    y0: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    x1: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    y1: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_bbox(self) -> ProjectedSourceAnchor:
        bbox = (self.x0, self.y0, self.x1, self.y1)
        if any(value is not None for value in bbox) and any(value is None for value in bbox):
            raise ValueError("projected source anchor bbox coordinates must be provided together")
        if self.x0 is not None and self.x1 is not None and self.x1 < self.x0:
            raise ValueError("projected source anchor x1 must be >= x0")
        if self.y0 is not None and self.y1 is not None and self.y1 < self.y0:
            raise ValueError("projected source anchor y1 must be >= y0")
        return self


class CanonicalArtifactIndexReference(SchemaModel):
    registry_id: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
    artifact_hash: ContentHash
    schema_version: str = Field(min_length=1, max_length=64)
    serialization_version: str = Field(min_length=1, max_length=32)
    content_encoding: ArtifactContentEncoding
    stored_hash: ContentHash
    stored_byte_size: int = Field(gt=0)


class RetrievalUnitIndexDocument(SchemaModel):
    """Backend-neutral search row; canonical structure remains in the artifact."""

    id: Identifier
    retrieval_unit_id: Identifier
    doc_id: Identifier
    kb_id: Identifier
    docnm_kwd: str
    chunk_order_int: int = Field(ge=0)
    content_hash: ContentHash
    source_revision: Identifier | None = None
    subdocument_id: Identifier | None = None
    element_ids: tuple[Identifier, ...] = Field(min_length=1)
    logical_unit_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    context_path: tuple[ProjectedContextNode, ...] = Field(default_factory=tuple)
    source_anchors: tuple[ProjectedSourceAnchor, ...] = Field(min_length=1)
    canonical_artifact_ref: CanonicalArtifactIndexReference
    unit_type: RetrievalUnitType
    language: str = Field(min_length=2, max_length=64)
    document_text_quality: Confidence | None = None
    document_order_quality: Confidence | None = None
    document_structure_quality: Confidence | None = None
    document_overall_quality: Confidence | None = None
    text_anomaly_count: int = Field(ge=0)
    has_text_anomaly: bool
    quality_multiplier: Confidence
    content_with_weight: str = Field(min_length=1)
    content_ltks: str
    content_sm_ltks: str
    token_num: int = Field(ge=1)
    projection_version: str = Field(default=RETRIEVAL_UNIT_INDEX_PROJECTOR_VERSION, min_length=1, max_length=32)
    tokenizer_version: str = Field(min_length=1, max_length=128)
    available_int: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def validate_identity_and_coverage(self) -> RetrievalUnitIndexDocument:
        if self.id != self.retrieval_unit_id:
            raise ValueError("index id must equal retrieval_unit_id")
        if len(self.element_ids) != len(set(self.element_ids)):
            raise ValueError("index element_ids must be unique")
        if len(self.logical_unit_ids) != len(set(self.logical_unit_ids)):
            raise ValueError("index logical_unit_ids must be unique")
        context_ids = [item.id for item in self.context_path]
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("index context_path ids must be unique")
        anchor_keys = [anchor.model_dump_json() for anchor in self.source_anchors]
        if len(anchor_keys) != len(set(anchor_keys)):
            raise ValueError("index source_anchors must be unique")
        anchor_elements = {anchor.element_id for anchor in self.source_anchors}
        if anchor_elements != set(self.element_ids):
            raise ValueError("index source_anchors must cover exactly the indexed element_ids")
        return self

    def to_index_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


class RetrievalUnitProjectionResult(SchemaModel):
    version: str = RETRIEVAL_UNIT_INDEX_PROJECTOR_VERSION
    document_id: Identifier
    content_hash: ContentHash
    canonical_artifact_registry_id: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
    documents: tuple[RetrievalUnitIndexDocument, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_unique_documents(self) -> RetrievalUnitProjectionResult:
        ids = [document.id for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieval index projection contains duplicate ids")
        return self


class RetrievalUnitProjector:
    version = RETRIEVAL_UNIT_INDEX_PROJECTOR_VERSION

    def __init__(
        self,
        tokenizer: Callable[[str], str],
        fine_grained_tokenizer: Callable[[str], str],
        *,
        tokenizer_version: str,
        quality_policy: RetrievalQualityPolicy | None = None,
    ) -> None:
        if not callable(tokenizer) or not callable(fine_grained_tokenizer):
            raise TypeError("retrieval index projector tokenizers must be callable")
        if not tokenizer_version or tokenizer_version.strip() != tokenizer_version or len(tokenizer_version) > 128:
            raise ValueError("tokenizer_version must be a trimmed non-blank string <= 128 characters")
        self._tokenizer = tokenizer
        self._fine_grained_tokenizer = fine_grained_tokenizer
        self._tokenizer_version = tokenizer_version
        self._quality_policy = quality_policy if quality_policy is not None else RetrievalQualityPolicy()

    def project(
        self,
        document: CanonicalDocument,
        units: Sequence[RetrievalUnit],
        *,
        kb_id: str,
        artifact_reference: CanonicalArtifactReference,
        artifact_registry_id: str,
    ) -> RetrievalUnitProjectionResult:
        if artifact_reference.content_hash != document.content_hash:
            raise RetrievalUnitProjectionError("canonical artifact content_hash does not match document")
        if artifact_reference.schema_version != document.schema_version:
            raise RetrievalUnitProjectionError("canonical artifact schema_version does not match document")
        canonical_bytes = serialize_canonical_document(document)
        if artifact_reference.artifact_hash != canonical_artifact_hash(canonical_bytes) or artifact_reference.byte_size != len(canonical_bytes):
            raise RetrievalUnitProjectionError("canonical artifact does not contain the exact document being projected")
        if re.fullmatch(r"[0-9a-f]{64}", artifact_registry_id) is None:
            raise RetrievalUnitProjectionError("artifact_registry_id must be a lowercase 64-character hex digest")

        element_order = {element.id: element.order for element in document.elements}
        validated_units = tuple(unit.validate_against_document(document) for unit in units)
        ordered_units = sorted(
            validated_units,
            key=lambda unit: (
                min(element_order[element_id] for element_id in unit.element_ids),
                unit.id,
            ),
        )
        artifact = CanonicalArtifactIndexReference(
            registry_id=artifact_registry_id,
            artifact_hash=artifact_reference.artifact_hash,
            schema_version=artifact_reference.schema_version,
            serialization_version=artifact_reference.serialization_version,
            content_encoding=artifact_reference.content_encoding,
            stored_hash=artifact_reference.stored_hash,
            stored_byte_size=artifact_reference.stored_byte_size,
        )
        projected = tuple(
            self._project_unit(
                document,
                unit,
                kb_id=kb_id,
                artifact=artifact,
                chunk_order=chunk_order,
            )
            for chunk_order, unit in enumerate(ordered_units)
        )
        return RetrievalUnitProjectionResult(
            document_id=document.document_id,
            content_hash=document.content_hash,
            canonical_artifact_registry_id=artifact_registry_id,
            documents=projected,
        )

    def _project_unit(
        self,
        document: CanonicalDocument,
        unit: RetrievalUnit,
        *,
        kb_id: str,
        artifact: CanonicalArtifactIndexReference,
        chunk_order: int,
    ) -> RetrievalUnitIndexDocument:
        content_ltks = self._tokenize("tokenizer", self._tokenizer, unit.retrieval_text)
        content_sm_ltks = self._tokenize("fine_grained_tokenizer", self._fine_grained_tokenizer, content_ltks)
        lexical = document.quality.lexical
        language = lexical.detected_language.value if lexical is not None else (document.metadata.language or "unknown")
        anomaly_counts = lexical.element_anomaly_counts if lexical is not None else {}
        text_anomaly_count = sum(value for element_id in unit.element_ids if isinstance((value := anomaly_counts.get(element_id, 0)), int))
        document_quality = document.quality.overall if document.quality.overall is not None else 1.0
        quality_multiplier = max(
            self._quality_policy.minimum_multiplier,
            document_quality - self._quality_policy.anomaly_penalty_per_occurrence * text_anomaly_count,
        )
        return RetrievalUnitIndexDocument(
            id=unit.id,
            retrieval_unit_id=unit.id,
            doc_id=unit.document_id,
            kb_id=kb_id,
            docnm_kwd=document.metadata.source_name or document.metadata.title or document.document_id,
            chunk_order_int=chunk_order,
            content_hash=unit.content_hash,
            source_revision=unit.source_revision,
            subdocument_id=unit.subdocument_id,
            element_ids=unit.element_ids,
            logical_unit_ids=unit.logical_unit_ids,
            context_path=tuple(
                ProjectedContextNode(
                    id=item.id,
                    type=item.type,
                    label=item.label,
                    source=item.source,
                    confidence=item.confidence,
                )
                for item in unit.context_path
            ),
            source_anchors=tuple(self._project_anchor(anchor) for anchor in unit.source_anchors),
            canonical_artifact_ref=artifact,
            unit_type=unit.unit_type,
            language=language,
            document_text_quality=document.quality.text_quality,
            document_order_quality=document.quality.order_quality,
            document_structure_quality=document.quality.structure_quality,
            document_overall_quality=document.quality.overall,
            text_anomaly_count=text_anomaly_count,
            has_text_anomaly=text_anomaly_count > 0,
            quality_multiplier=quality_multiplier,
            content_with_weight=unit.retrieval_text,
            content_ltks=content_ltks,
            content_sm_ltks=content_sm_ltks,
            token_num=unit.token_count,
            tokenizer_version=self._tokenizer_version,
        )

    @staticmethod
    def _project_anchor(anchor: SourceAnchor) -> ProjectedSourceAnchor:
        bbox = anchor.bbox
        return ProjectedSourceAnchor(
            element_id=anchor.element_id,
            location_source=anchor.location_source,
            page=anchor.page,
            x0=bbox.x0 if bbox is not None else None,
            y0=bbox.y0 if bbox is not None else None,
            x1=bbox.x1 if bbox is not None else None,
            y1=bbox.y1 if bbox is not None else None,
            start_char=anchor.start_char,
            end_char=anchor.end_char,
            line_start=anchor.line_start,
            line_end=anchor.line_end,
        )

    @staticmethod
    def _tokenize(
        name: str,
        tokenizer: Callable[[str], str],
        text: str,
    ) -> str:
        value = tokenizer(text)
        if not isinstance(value, str):
            raise RetrievalUnitProjectionError(f"{name} must return str")
        return value


__all__ = [
    "RETRIEVAL_UNIT_INDEX_PROJECTOR_VERSION",
    "CanonicalArtifactIndexReference",
    "ProjectedContextNode",
    "ProjectedSourceAnchor",
    "RetrievalQualityPolicy",
    "RetrievalUnitIndexDocument",
    "RetrievalUnitProjectionError",
    "RetrievalUnitProjectionResult",
    "RetrievalUnitProjector",
]
