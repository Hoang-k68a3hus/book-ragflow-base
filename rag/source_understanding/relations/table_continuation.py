from __future__ import annotations

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import NormalizedCoordinate, SchemaModel

TABLE_CONTINUATION_EVIDENCE_ATTRIBUTE = "table_continuation_evidence"
TABLE_CONTINUATION_CONTRACT_VERSION = "adjacent-page-table-continuation-v1"
TABLE_CONTINUATION_EVIDENCE_COMPARISON_TOLERANCE = 1e-9


class TableContinuationEvidence(SchemaModel):
    """Adapter-provided geometry evidence for one accepted table fragment.

    This is deliberately evidence, not a source fact.  The relation builder
    validates it again before it can produce an inferred CONTINUES relation.
    Coordinates are page-relative so the evidence remains comparable across
    pages with small size differences.
    """

    version: str = TABLE_CONTINUATION_CONTRACT_VERSION
    page: int = Field(ge=1)
    bbox: tuple[
        NormalizedCoordinate,
        NormalizedCoordinate,
        NormalizedCoordinate,
        NormalizedCoordinate,
    ]
    column_boundaries: tuple[NormalizedCoordinate, ...] = Field(min_length=2)
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    topology: str = Field(min_length=1, max_length=128)
    orientation: str | None = Field(default=None, max_length=32)
    leading_row_fingerprint: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_geometry(self) -> TableContinuationEvidence:
        x0, y0, x1, y1 = self.bbox
        if x1 < x0 or y1 < y0:
            raise ValueError("table continuation bbox must have non-negative extents")
        if any(right <= left for left, right in zip(self.column_boundaries, self.column_boundaries[1:])):
            raise ValueError("table continuation column boundaries must be strictly ordered")
        if self.column_boundaries[0] < x0 or self.column_boundaries[-1] > x1:
            raise ValueError("table continuation columns must lie inside table bbox")
        if self.topology.strip() != self.topology:
            raise ValueError("table continuation topology must be trimmed")
        if self.orientation is not None and self.orientation.strip() != self.orientation:
            raise ValueError("table continuation orientation must be trimmed")
        return self
