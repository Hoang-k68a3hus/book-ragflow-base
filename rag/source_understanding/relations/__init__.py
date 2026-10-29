"""Deterministic structural relation construction."""

from .builder import (
    RELATION_BUILDER_VERSION,
    RelationBuildDiagnostic,
    RelationBuildError,
    RelationBuildPolicy,
    RelationBuildResult,
    RelationDiagnosticOutcome,
    StructuralRelationBuilder,
)
from .table_continuation import (
    TABLE_CONTINUATION_CONTRACT_VERSION,
    TABLE_CONTINUATION_EVIDENCE_ATTRIBUTE,
    TABLE_CONTINUATION_EVIDENCE_COMPARISON_TOLERANCE,
    TableContinuationEvidence,
)

__all__ = [
    "RELATION_BUILDER_VERSION",
    "TABLE_CONTINUATION_CONTRACT_VERSION",
    "TABLE_CONTINUATION_EVIDENCE_ATTRIBUTE",
    "TABLE_CONTINUATION_EVIDENCE_COMPARISON_TOLERANCE",
    "RelationBuildDiagnostic",
    "RelationBuildError",
    "RelationBuildPolicy",
    "RelationBuildResult",
    "RelationDiagnosticOutcome",
    "StructuralRelationBuilder",
    "TableContinuationEvidence",
]
