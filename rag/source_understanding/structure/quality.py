from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import Confidence, SchemaModel
from rag.source_understanding.schemas.document import DocumentQuality
from rag.source_understanding.schemas.element import Element, ElementType

from .boundary import BoundaryClass, BoundarySet
from .grouping import GroupingResult
from .hierarchy import HierarchyResult
from .integrity import unresolved_integrity_boundary_ids

STRUCTURE_QUALITY_VERSION = "2"


class StructureQualityError(ValueError):
    """Structure quality cannot be estimated from inconsistent stage outputs."""


class StructureQualityPolicy(SchemaModel):
    version: str = STRUCTURE_QUALITY_VERSION
    accounted_weight: Confidence = 0.40
    boundary_certainty_weight: Confidence = 0.30
    integrity_resolution_weight: Confidence = 0.30
    unknown_type_penalty: Confidence = 0.50
    low_accounted_warning_threshold: Confidence = 0.80
    high_unknown_boundary_warning_threshold: Confidence = 0.20
    high_unknown_type_warning_threshold: Confidence = 0.20

    @model_validator(mode="after")
    def validate_weights(self) -> StructureQualityPolicy:
        total = self.accounted_weight + self.boundary_certainty_weight + self.integrity_resolution_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError("structure quality component weights must sum to 1.0")
        return self


class StructureQualityMetrics(SchemaModel):
    element_count: int = Field(ge=1)
    grouped_element_count: int = Field(ge=0)
    context_anchor_count: int = Field(ge=0)
    structurally_accounted_count: int = Field(ge=0)
    structurally_accounted_ratio: Confidence
    unknown_element_count: int = Field(ge=0)
    unknown_element_ratio: Confidence
    boundary_count: int = Field(ge=0)
    unknown_boundary_count: int = Field(ge=0)
    boundary_certainty_ratio: Confidence
    unresolved_integrity_count: int = Field(ge=0)
    integrity_resolution_ratio: Confidence
    context_node_count: int = Field(ge=0)
    context_assigned_element_count: int = Field(ge=0)
    context_assignment_ratio: Confidence
    logical_unit_count: int = Field(ge=0)
    subdocument_count: int = Field(ge=0)


class StructureQualityReport(SchemaModel):
    version: str = STRUCTURE_QUALITY_VERSION
    policy: StructureQualityPolicy
    metrics: StructureQualityMetrics
    quality: DocumentQuality


class StructureQualityEstimator:
    """Estimate structural quality without rewarding hierarchy merely for existing."""

    version: str = STRUCTURE_QUALITY_VERSION

    def __init__(self, policy: StructureQualityPolicy | None = None) -> None:
        self._policy = policy if policy is not None else StructureQualityPolicy()

    def estimate(
        self,
        elements: Sequence[Element],
        boundary_set: BoundarySet,
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
    ) -> StructureQualityReport:
        snapshot = tuple(elements)
        self._validate_inputs(snapshot, boundary_set, grouping_result, hierarchy_result)

        element_ids = {element.id for element in snapshot}
        grouped = {element_id for unit in grouping_result.logical_units for element_id in unit.element_ids}
        context_anchors = self._context_anchor_ids(hierarchy_result, element_ids)
        accounted = grouped | context_anchors

        unknown_elements = sum(element.type == ElementType.UNKNOWN for element in snapshot)
        boundary_count = len(boundary_set.boundaries)
        unknown_boundaries = sum(boundary.classification == BoundaryClass.UNKNOWN for boundary in boundary_set.boundaries)
        unresolved_integrity_ids = unresolved_integrity_boundary_ids(
            boundary_set,
            grouping_result,
        )
        unresolved_integrity = len(unresolved_integrity_ids)
        context_assigned = sum(bool(assignment.context_node_ids) for assignment in hierarchy_result.assignments)

        element_count = len(snapshot)
        accounted_ratio = len(accounted) / element_count
        unknown_element_ratio = unknown_elements / element_count
        if boundary_count:
            boundary_certainty_ratio = 1.0 - (unknown_boundaries / boundary_count)
            integrity_resolution_ratio = 1.0 - (unresolved_integrity / boundary_count)
        else:
            boundary_certainty_ratio = 1.0
            integrity_resolution_ratio = 1.0
        context_assignment_ratio = context_assigned / element_count

        base_score = (
            self._policy.accounted_weight * accounted_ratio + self._policy.boundary_certainty_weight * boundary_certainty_ratio + self._policy.integrity_resolution_weight * integrity_resolution_ratio
        )
        structure_quality = base_score * (1.0 - self._policy.unknown_type_penalty * unknown_element_ratio)
        structure_quality = min(1.0, max(0.0, structure_quality))

        warnings: list[str] = []
        if accounted_ratio < self._policy.low_accounted_warning_threshold:
            warnings.append("low structural accounting coverage")
        unknown_boundary_ratio = 0.0 if boundary_count == 0 else unknown_boundaries / boundary_count
        if unknown_boundary_ratio > self._policy.high_unknown_boundary_warning_threshold:
            warnings.append("high unresolved boundary ratio")
        if unknown_element_ratio > self._policy.high_unknown_type_warning_threshold:
            warnings.append("high UNKNOWN element ratio")
        if unresolved_integrity:
            warnings.append("content-integrity continuity remains unresolved")

        metrics = StructureQualityMetrics(
            element_count=element_count,
            grouped_element_count=len(grouped),
            context_anchor_count=len(context_anchors),
            structurally_accounted_count=len(accounted),
            structurally_accounted_ratio=accounted_ratio,
            unknown_element_count=unknown_elements,
            unknown_element_ratio=unknown_element_ratio,
            boundary_count=boundary_count,
            unknown_boundary_count=unknown_boundaries,
            boundary_certainty_ratio=boundary_certainty_ratio,
            unresolved_integrity_count=unresolved_integrity,
            integrity_resolution_ratio=integrity_resolution_ratio,
            context_node_count=len(hierarchy_result.context_nodes),
            context_assigned_element_count=context_assigned,
            context_assignment_ratio=context_assignment_ratio,
            logical_unit_count=len(grouping_result.logical_units),
            subdocument_count=len(grouping_result.subdocuments),
        )

        quality = DocumentQuality(
            structure_quality=structure_quality,
            warnings=tuple(warnings),
            metrics={
                "structure_quality_version": self.version,
                "policy_version": self._policy.version,
                "structure_mode": hierarchy_result.structure.mode.value,
                "structurally_accounted_ratio": accounted_ratio,
                "unknown_element_ratio": unknown_element_ratio,
                "unknown_boundary_ratio": unknown_boundary_ratio,
                "integrity_resolution_ratio": integrity_resolution_ratio,
                "unresolved_integrity_boundary_ids": list(unresolved_integrity_ids),
                "context_assignment_ratio": context_assignment_ratio,
            },
        )
        return StructureQualityReport(
            policy=self._policy,
            metrics=metrics,
            quality=quality,
        )

    @staticmethod
    def _context_anchor_ids(
        hierarchy_result: HierarchyResult,
        valid_element_ids: set[str],
    ) -> set[str]:
        anchors: set[str] = set()
        for node in hierarchy_result.context_nodes:
            anchor = node.attributes.get("anchor_element_id")
            if isinstance(anchor, str) and anchor in valid_element_ids:
                anchors.add(anchor)
        return anchors

    @staticmethod
    def _validate_inputs(
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
    ) -> None:
        if not elements:
            raise StructureQualityError("cannot estimate structure quality for an empty source")
        expected = len(elements)
        if boundary_set.element_count != expected:
            raise StructureQualityError("boundary element_count does not match quality input")
        if grouping_result.element_count != expected:
            raise StructureQualityError("grouping element_count does not match quality input")
        if hierarchy_result.element_count != expected:
            raise StructureQualityError("hierarchy element_count does not match quality input")

        element_ids = [element.id for element in elements]
        if len(element_ids) != len(set(element_ids)):
            raise StructureQualityError("quality input elements must have unique ids")
        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)) or orders != sorted(orders):
            raise StructureQualityError("quality input elements must follow canonical order")

        known = set(element_ids)
        grouped = {element_id for unit in grouping_result.logical_units for element_id in unit.element_ids}
        ungrouped = set(grouping_result.ungrouped_element_ids)
        if grouped - known or ungrouped - known:
            raise StructureQualityError("grouping result references unknown elements")
        if grouped & ungrouped:
            raise StructureQualityError("grouped and ungrouped element sets overlap")
        if grouped | ungrouped != known:
            raise StructureQualityError("grouping result must account for every element as grouped or ungrouped")

        assignment_ids = {assignment.element_id for assignment in hierarchy_result.assignments}
        if assignment_ids != known:
            raise StructureQualityError("hierarchy assignments must cover exactly the quality input elements")

        if len(boundary_set.boundaries) != max(0, expected - 1):
            raise StructureQualityError("boundary set does not cover every adjacent pair")
        for index, boundary in enumerate(boundary_set.boundaries):
            if boundary.left_element_id != elements[index].id or boundary.right_element_id != elements[index + 1].id:
                raise StructureQualityError("boundary set does not align with canonical element adjacency")
