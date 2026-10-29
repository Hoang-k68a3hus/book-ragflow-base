from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import Confidence, Identifier, JsonObject, SchemaModel
from rag.source_understanding.schemas.document import CanonicalDocument


class SemanticCoverageReport(SchemaModel):
    """Descriptive semantic coverage diagnostics, not an accuracy score."""

    target_count: int = Field(ge=0)
    annotated_target_count: int = Field(ge=0)
    annotation_count: int = Field(ge=0)
    coverage: Confidence | None = None
    mean_confidence: Confidence | None = None
    type_counts: JsonObject = Field(default_factory=dict)
    source_counts: JsonObject = Field(default_factory=dict)
    provider_counts: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_counts(self) -> SemanticCoverageReport:
        if self.annotated_target_count > self.target_count:
            raise ValueError("annotated_target_count cannot exceed target_count")
        return self


def evaluate_semantic_coverage(
    document: CanonicalDocument,
    target_ids: Iterable[Identifier],
) -> SemanticCoverageReport:
    ordered_targets = tuple(dict.fromkeys(target_ids))
    target_set = set(ordered_targets)
    annotations = tuple(annotation for annotation in document.semantic_annotations if annotation.target_id in target_set)
    annotated_targets = {annotation.target_id for annotation in annotations}

    confidence = None
    if annotations:
        confidence = sum(annotation.confidence for annotation in annotations) / len(annotations)

    coverage = None
    if ordered_targets:
        coverage = len(annotated_targets) / len(ordered_targets)

    type_counts = Counter(annotation.type.value for annotation in annotations)
    source_counts = Counter(annotation.source.value for annotation in annotations)
    provider_counts = Counter(str(annotation.metadata.get("semantic_provider", "unknown")) for annotation in annotations)

    return SemanticCoverageReport(
        target_count=len(ordered_targets),
        annotated_target_count=len(annotated_targets),
        annotation_count=len(annotations),
        coverage=coverage,
        mean_confidence=confidence,
        type_counts=dict(sorted(type_counts.items())),
        source_counts=dict(sorted(source_counts.items())),
        provider_counts=dict(sorted(provider_counts.items())),
    )
