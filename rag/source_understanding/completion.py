from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from rag.source_understanding.profiling.regions import ContentRegionSegmentationResult
from rag.source_understanding.schemas.context import Confidence, Identifier, JsonObject, SchemaModel, StructureMode
from rag.source_understanding.schemas.document import CanonicalDocument
from rag.source_understanding.schemas.element import ElementType
from rag.source_understanding.semantics.annotator import SemanticAnnotationResult
from rag.source_understanding.structure.boundary import BoundarySet
from rag.source_understanding.structure.grouping import GroupingResult
from rag.source_understanding.structure.hierarchy import HierarchyResult
from rag.source_understanding.structure.integrity import (
    IntegrityConsolidationReport,
    unresolved_integrity_boundary_ids,
)
from rag.source_understanding.structure.quality import StructureQualityReport

UNDERSTANDING_COMPLETION_VERSION = "2"


_INTEGRITY_SENSITIVE_TYPES = frozenset(
    {
        ElementType.TABLE,
        ElementType.TABLE_ROW,
        ElementType.TABLE_CELL,
        ElementType.LIST,
        ElementType.LIST_ITEM,
        ElementType.CODE,
        ElementType.FORMULA,
        ElementType.KEY_VALUE,
    }
)


class UnderstandingCompletionMetrics(SchemaModel):
    element_count: int = Field(ge=1)
    grouped_element_count: int = Field(ge=0)
    ungrouped_element_count: int = Field(ge=0)
    ungrouped_element_ratio: Confidence
    unknown_element_count: int = Field(ge=0)
    unknown_element_ratio: Confidence
    unknown_boundary_count: int = Field(ge=0)
    unknown_boundary_ratio: Confidence
    unresolved_integrity_count: int = Field(ge=0)
    integrity_sensitive_ungrouped_count: int = Field(ge=0)
    logical_unit_count: int = Field(ge=0)
    context_node_count: int = Field(ge=0)
    context_assignment_ratio: Confidence
    subdocument_count: int = Field(ge=0)
    region_count: int = Field(ge=0)
    region_coverage_ratio: Confidence | None = None
    unknown_region_structure_count: int = Field(ge=0)
    semantic_annotation_count: int = Field(ge=0)
    semantic_target_coverage: Confidence | None = None
    adapter_diagnostic_count: int = Field(default=0, ge=0)
    adapter_warning_count: int = Field(default=0, ge=0)
    adapter_structural_issue_count: int = Field(default=0, ge=0)


class UnderstandingCompletionReport(SchemaModel):
    version: str = UNDERSTANDING_COMPLETION_VERSION
    document_id: Identifier
    structural_pipeline_complete: bool = True
    structural_ready: bool
    structure_mode: StructureMode
    semantic_status: str = Field(min_length=1, max_length=64)
    metrics: UnderstandingCompletionMetrics
    unresolved_integrity_boundary_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    integrity_sensitive_ungrouped_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    diagnostics: JsonObject = Field(default_factory=dict)


class UnderstandingCompletionBuilder:
    """Summarize pipeline completion without pretending coverage is model accuracy.

    Adapter diagnostics are intentionally accepted structurally rather than via a
    concrete adapter type. The core understanding package must not depend back on
    optional source-adapter packages; diagnostics only need ``code``, ``level``
    and ``affects_structural_completeness`` attributes.
    """

    version = UNDERSTANDING_COMPLETION_VERSION

    def build(
        self,
        *,
        document: CanonicalDocument,
        boundary_set: BoundarySet,
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
        integrity_report: IntegrityConsolidationReport,
        quality_report: StructureQualityReport,
        region_result: ContentRegionSegmentationResult | None,
        semantic_status: str,
        semantic_result: SemanticAnnotationResult | None,
        adapter_diagnostics: Sequence[object] = (),
    ) -> UnderstandingCompletionReport:
        self._validate_counts(
            document,
            boundary_set,
            grouping_result,
            hierarchy_result,
            integrity_report,
            quality_report,
            region_result,
        )
        element_by_id = {element.id: element for element in document.elements}
        grouped_ids = {element_id for unit in grouping_result.logical_units for element_id in unit.element_ids}
        ungrouped_ids = tuple(grouping_result.ungrouped_element_ids)
        integrity_sensitive_ungrouped = tuple(element_id for element_id in ungrouped_ids if element_by_id[element_id].type in _INTEGRITY_SENSITIVE_TYPES)
        unresolved_integrity = unresolved_integrity_boundary_ids(
            boundary_set,
            grouping_result,
        )

        element_count = len(document.elements)
        unknown_elements = sum(element.type == ElementType.UNKNOWN for element in document.elements)
        unknown_boundaries = sum(getattr(boundary.classification, "value", str(boundary.classification)) == "UNKNOWN" for boundary in boundary_set.boundaries)
        boundary_count = len(boundary_set.boundaries)
        context_assigned = sum(bool(assignment.context_node_ids) for assignment in hierarchy_result.assignments)

        region_count = len(document.regions)
        region_coverage_ratio: float | None = None
        unknown_region_structure_count = 0
        if region_count:
            covered = {element_id for region in document.regions for element_id in region.element_ids}
            region_coverage_ratio = len(covered) / element_count
            unknown_region_structure_count = sum(region.structure.mode == StructureMode.UNKNOWN for region in document.regions)

        semantic_target_coverage = None
        if semantic_result is not None:
            semantic_target_coverage = semantic_result.coverage.coverage

        adapter_diagnostic_snapshot = tuple(adapter_diagnostics)
        adapter_warning_count = sum(self._diagnostic_level(diagnostic) == "WARNING" for diagnostic in adapter_diagnostic_snapshot)
        adapter_structural_issues = tuple(diagnostic for diagnostic in adapter_diagnostic_snapshot if bool(getattr(diagnostic, "affects_structural_completeness", False)))

        mixed_region_ready = document.structure.mode != StructureMode.MIXED or (region_count > 0 and region_coverage_ratio == 1.0)
        structural_ready = not unresolved_integrity and not integrity_sensitive_ungrouped and mixed_region_ready and not adapter_structural_issues

        warnings = list(quality_report.quality.warnings)
        if integrity_sensitive_ungrouped:
            warnings.append("integrity-sensitive elements remain ungrouped")
        if document.structure.mode == StructureMode.MIXED and not mixed_region_ready:
            warnings.append("MIXED structure does not have complete region routing")
        if adapter_structural_issues:
            warnings.append("source adapter reported unresolved structural source constructs")
        if semantic_status == "FAILED_OPTIONAL":
            warnings.append("optional semantic enrichment failed; structural document remains valid")
        warnings = list(dict.fromkeys(warnings))

        metrics = UnderstandingCompletionMetrics(
            element_count=element_count,
            grouped_element_count=len(grouped_ids),
            ungrouped_element_count=len(ungrouped_ids),
            ungrouped_element_ratio=len(ungrouped_ids) / element_count,
            unknown_element_count=unknown_elements,
            unknown_element_ratio=unknown_elements / element_count,
            unknown_boundary_count=unknown_boundaries,
            unknown_boundary_ratio=(unknown_boundaries / boundary_count if boundary_count else 0.0),
            unresolved_integrity_count=len(unresolved_integrity),
            integrity_sensitive_ungrouped_count=len(integrity_sensitive_ungrouped),
            logical_unit_count=len(grouping_result.logical_units),
            context_node_count=len(hierarchy_result.context_nodes),
            context_assignment_ratio=context_assigned / element_count,
            subdocument_count=len(grouping_result.subdocuments),
            region_count=region_count,
            region_coverage_ratio=region_coverage_ratio,
            unknown_region_structure_count=unknown_region_structure_count,
            semantic_annotation_count=len(document.semantic_annotations),
            semantic_target_coverage=semantic_target_coverage,
            adapter_diagnostic_count=len(adapter_diagnostic_snapshot),
            adapter_warning_count=adapter_warning_count,
            adapter_structural_issue_count=len(adapter_structural_issues),
        )
        return UnderstandingCompletionReport(
            document_id=document.document_id,
            structural_ready=structural_ready,
            structure_mode=document.structure.mode,
            semantic_status=semantic_status,
            metrics=metrics,
            unresolved_integrity_boundary_ids=unresolved_integrity,
            integrity_sensitive_ungrouped_ids=integrity_sensitive_ungrouped,
            warnings=tuple(warnings),
            diagnostics={
                "completion_is_not_accuracy": True,
                "structure_quality_version": quality_report.version,
                "integrity_consolidation_version": integrity_report.version,
                "region_segmentation_version": (region_result.version if region_result is not None else None),
                "adapter_structural_issue_codes": [self._diagnostic_code(diagnostic) for diagnostic in adapter_structural_issues],
            },
        )

    @staticmethod
    def _diagnostic_level(diagnostic: object) -> str | None:
        level = getattr(diagnostic, "level", None)
        value = getattr(level, "value", level)
        return value if isinstance(value, str) else None

    @staticmethod
    def _diagnostic_code(diagnostic: object) -> str:
        code = getattr(diagnostic, "code", None)
        return code if isinstance(code, str) and code else type(diagnostic).__name__

    @staticmethod
    def _validate_counts(
        document: CanonicalDocument,
        boundary_set: BoundarySet,
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
        integrity_report: IntegrityConsolidationReport,
        quality_report: StructureQualityReport,
        region_result: ContentRegionSegmentationResult | None,
    ) -> None:
        expected = len(document.elements)
        counts = {
            "boundary": boundary_set.element_count,
            "grouping": grouping_result.element_count,
            "hierarchy": hierarchy_result.element_count,
            "integrity": integrity_report.element_count,
            "quality": quality_report.metrics.element_count,
        }
        if region_result is not None:
            counts["region"] = region_result.element_count
        mismatched = {name: value for name, value in counts.items() if value != expected}
        if mismatched:
            raise ValueError(f"completion report stage-count mismatch: expected {expected}, got {mismatched}")
