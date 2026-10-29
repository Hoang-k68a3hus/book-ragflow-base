from __future__ import annotations

from collections.abc import Sequence

from rag.source_understanding.profiling.regions import ContentRegionSegmentationResult
from rag.source_understanding.schemas.document import ContentRegion, DocumentStructure
from rag.source_understanding.schemas.logical_unit import LogicalUnit

from .grouping import GroupingResult
from .hierarchy import HierarchyResult

REGION_ROUTING_VERSION = "2"


class RegionRoutingError(ValueError):
    """Structural units cannot be safely aligned with content regions."""


class RegionRouter:
    """Attach region ownership without changing logical-unit membership or context."""

    version: str = REGION_ROUTING_VERSION

    def apply(
        self,
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
        region_result: ContentRegionSegmentationResult,
    ) -> tuple[GroupingResult, HierarchyResult]:
        if grouping_result.element_count != region_result.element_count:
            raise RegionRoutingError("grouping and region element_count values disagree")
        if hierarchy_result.element_count != region_result.element_count:
            raise RegionRoutingError("hierarchy and region element_count values disagree")

        grouping = self.assign_grouping(grouping_result, region_result.regions)
        hierarchy = self.apply_structure(hierarchy_result, region_result.structure)
        return grouping, hierarchy

    @staticmethod
    def assign_grouping(
        grouping_result: GroupingResult,
        regions: Sequence[ContentRegion],
    ) -> GroupingResult:
        region_snapshot = tuple(regions)
        owner: dict[str, str] = {}
        for region in region_snapshot:
            for element_id in region.element_ids:
                previous = owner.get(element_id)
                if previous is not None:
                    raise RegionRoutingError(f"element {element_id!r} belongs to multiple content regions")
                owner[element_id] = region.id

        known_elements = {element_id for unit in grouping_result.logical_units for element_id in unit.element_ids} | set(grouping_result.ungrouped_element_ids)
        unknown_region_elements = set(owner) - known_elements
        if unknown_region_elements:
            raise RegionRoutingError(f"content regions reference elements outside grouping result: {sorted(unknown_region_elements)}")
        missing_region_elements = known_elements - set(owner)
        if missing_region_elements:
            raise RegionRoutingError(f"content regions must cover every canonical element exactly once; missing {sorted(missing_region_elements)}")

        updated_units: list[LogicalUnit] = []
        for unit in grouping_result.logical_units:
            region_ids = {owner[element_id] for element_id in unit.element_ids if element_id in owner}
            if len(region_ids) > 1:
                raise RegionRoutingError(f"logical unit {unit.id!r} crosses content region boundaries: {sorted(region_ids)}")
            resolved_region_id = next(iter(region_ids), None)
            if unit.region_id is not None and unit.region_id != resolved_region_id:
                raise RegionRoutingError(f"logical unit {unit.id!r} carries conflicting region_id {unit.region_id!r} != {resolved_region_id!r}")
            data = unit.model_dump(mode="python")
            data["region_id"] = resolved_region_id
            updated_units.append(LogicalUnit.model_validate(data))

        data = grouping_result.model_dump(mode="python")
        data["logical_units"] = tuple(updated_units)
        return GroupingResult.model_validate(data)

    @staticmethod
    def apply_structure(
        hierarchy_result: HierarchyResult,
        structure: DocumentStructure,
    ) -> HierarchyResult:
        data = hierarchy_result.model_dump(mode="python")
        data["structure"] = structure
        return HierarchyResult.model_validate(data)
