from __future__ import annotations

import itertools

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import ContextNode, SchemaModel
from rag.source_understanding.schemas.logical_unit import LogicalUnit

from .grouping import GroupingResult
from .hierarchy import HierarchyResult

CONTEXT_INTEGRATION_VERSION = "1"


class ContextIntegrationError(ValueError):
    """Grouping and hierarchy outputs cannot be reconciled safely."""


class ContextIntegrationResult(SchemaModel):
    version: str = CONTEXT_INTEGRATION_VERSION
    element_count: int = Field(ge=1)
    grouping_version: str
    hierarchy_version: str
    logical_units: tuple[LogicalUnit, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_unique_units(self) -> ContextIntegrationResult:
        unit_ids = [unit.id for unit in self.logical_units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("context integration logical unit ids must be unique")
        return self


class ContextIntegrator:
    """Attach only context shared by every member element of a LogicalUnit."""

    version: str = CONTEXT_INTEGRATION_VERSION

    def integrate(
        self,
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
    ) -> ContextIntegrationResult:
        self._validate_inputs(grouping_result, hierarchy_result)

        assignments = {assignment.element_id: assignment.context_node_ids for assignment in hierarchy_result.assignments}
        nodes = {node.id: node for node in hierarchy_result.context_nodes}
        self._validate_assignment_paths(assignments, nodes)

        integrated: list[LogicalUnit] = []
        for unit in grouping_result.logical_units:
            try:
                member_paths = tuple(assignments[element_id] for element_id in unit.element_ids)
            except KeyError as exc:
                raise ContextIntegrationError(f"logical unit {unit.id!r} references element without hierarchy assignment: {exc.args[0]!r}") from exc

            shared_path = self._longest_common_prefix(member_paths)
            if unit.context_node_ids and unit.context_node_ids != shared_path:
                raise ContextIntegrationError(f"logical unit {unit.id!r} already carries context that disagrees with the hierarchy-derived common context")

            data = unit.model_dump(mode="python")
            data["context_node_ids"] = shared_path
            integrated.append(LogicalUnit.model_validate(data))

        return ContextIntegrationResult(
            element_count=grouping_result.element_count,
            grouping_version=grouping_result.version,
            hierarchy_version=hierarchy_result.version,
            logical_units=tuple(integrated),
        )

    @staticmethod
    def _longest_common_prefix(
        paths: tuple[tuple[str, ...], ...],
    ) -> tuple[str, ...]:
        if not paths:
            return ()
        prefix_length = min(len(path) for path in paths)
        index = 0
        while index < prefix_length:
            value = paths[0][index]
            if any(path[index] != value for path in paths[1:]):
                break
            index += 1
        return paths[0][:index]

    @staticmethod
    def _validate_inputs(
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
    ) -> None:
        if grouping_result.element_count != hierarchy_result.element_count:
            raise ContextIntegrationError("grouping and hierarchy element_count values do not match")

        assignment_ids = [assignment.element_id for assignment in hierarchy_result.assignments]
        if len(assignment_ids) != hierarchy_result.element_count:
            raise ContextIntegrationError("hierarchy assignments must cover every canonical element")
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ContextIntegrationError("hierarchy assignments contain duplicate element ids")

        known_assignments = set(assignment_ids)
        for unit in grouping_result.logical_units:
            missing = set(unit.element_ids) - known_assignments
            if missing:
                raise ContextIntegrationError(f"logical unit {unit.id!r} references elements without hierarchy assignments: {sorted(missing)}")

    @staticmethod
    def _validate_assignment_paths(
        assignments: dict[str, tuple[str, ...]],
        nodes: dict[str, ContextNode],
    ) -> None:
        for element_id, path in assignments.items():
            missing = set(path) - nodes.keys()
            if missing:
                raise ContextIntegrationError(f"hierarchy assignment for {element_id!r} references unknown context nodes: {sorted(missing)}")
            for parent_id, child_id in itertools.pairwise(path):
                if nodes[child_id].parent_id != parent_id:
                    raise ContextIntegrationError(f"hierarchy assignment for {element_id!r} is not a canonical parent-child path at {parent_id!r} -> {child_id!r}")
