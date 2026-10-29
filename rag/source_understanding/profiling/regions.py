from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import (
    Confidence,
    JsonObject,
    SchemaModel,
    StructureMode,
    StructureSource,
)
from rag.source_understanding.schemas.document import ContentRegion, DocumentStructure
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.schemas.logical_unit import LogicalUnitType

from .content_profiler import ContentCategory, content_category_for_element

if TYPE_CHECKING:
    from rag.source_understanding.structure.grouping import GroupingResult
    from rag.source_understanding.structure.hierarchy import HierarchyResult


CONTENT_REGION_SEGMENTER_VERSION = "4"
CONTENT_REGION_POLICY_VERSION = "1"


class ContentRegionSegmentationError(ValueError):
    """Elements cannot be partitioned into trustworthy content regions."""


class ContentRegionPolicy(SchemaModel):
    """Conservative deterministic routing policy, never token-size segmentation."""

    version: str = CONTENT_REGION_POLICY_VERSION
    region_confidence: Confidence = 0.80
    local_structure_confidence: Confidence = 0.70
    mixed_structure_confidence: Confidence = 0.70
    embedded_mixed_min_share: Confidence = 0.35
    bridge_categories: tuple[ContentCategory, ...] = (
        ContentCategory.BOILERPLATE,
        ContentCategory.SEPARATOR,
    )
    specialized_local_categories: tuple[ContentCategory, ...] = (
        ContentCategory.LIST,
        ContentCategory.DIALOGUE,
        ContentCategory.CODE,
        ContentCategory.TABLE,
        ContentCategory.QA,
        ContentCategory.FORMULA,
        ContentCategory.LOG,
        ContentCategory.KEY_VALUE,
        ContentCategory.VISUAL,
    )
    interaction_categories: tuple[ContentCategory, ...] = (
        ContentCategory.QA,
        ContentCategory.DIALOGUE,
        ContentCategory.LOG,
        ContentCategory.KEY_VALUE,
    )
    hierarchy_embeddable_categories: tuple[ContentCategory, ...] = (
        ContentCategory.LIST,
        ContentCategory.CODE,
        ContentCategory.TABLE,
        ContentCategory.FORMULA,
        ContentCategory.VISUAL,
    )

    @model_validator(mode="after")
    def validate_category_sets(self) -> ContentRegionPolicy:
        for field_name in (
            "bridge_categories",
            "specialized_local_categories",
            "interaction_categories",
            "hierarchy_embeddable_categories",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicate categories")
        if set(self.bridge_categories) & set(self.specialized_local_categories):
            raise ValueError("bridge categories cannot also be specialized local categories")
        return self


class ContentRegionSegmentationResult(SchemaModel):
    version: str = CONTENT_REGION_SEGMENTER_VERSION
    element_count: int = Field(ge=1)
    policy: ContentRegionPolicy
    regions: tuple[ContentRegion, ...] = Field(min_length=1)
    structure: DocumentStructure
    mixed: bool
    diagnostics: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> ContentRegionSegmentationResult:
        region_ids = [region.id for region in self.regions]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("content region ids must be unique")
        member_ids = [element_id for region in self.regions for element_id in region.element_ids]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("content regions must not overlap")
        if len(member_ids) != self.element_count:
            raise ValueError("content regions must cover every input element exactly once")
        if self.mixed != (self.structure.mode == StructureMode.MIXED):
            raise ValueError("mixed flag must agree with document structure mode")
        return self


@dataclass
class _RegionDraft:
    routing_category: ContentCategory
    elements: list[Element]
    bridge_element_ids: list[str]


_INTERACTION_UNIT_ROUTING: dict[LogicalUnitType, ContentCategory] = {
    LogicalUnitType.QA_PAIR: ContentCategory.QA,
    LogicalUnitType.DIALOGUE_SEGMENT: ContentCategory.DIALOGUE,
    LogicalUnitType.LOG_WINDOW: ContentCategory.LOG,
    LogicalUnitType.KEY_VALUE_GROUP: ContentCategory.KEY_VALUE,
}


class ContentRegionSegmenter:
    """Partition canonical order into contiguous modality regions.

    Regions are routing/quality units, not semantic topics. A category transition
    can open a region; token counts never do. Boilerplate and separators are
    conservatively attached to an adjacent source region so every element remains
    covered without inventing a semantic topic.

    Source-near ``Element.type`` remains authoritative for observed composition.
    Resolved structural units may refine only the *routing* category. In addition
    to interaction units, a blank source paragraph between fragments of one
    consolidated native LIST_GROUP may route as list continuity. That bridge is a
    derived structural interpretation; the paragraph remains a PARAGRAPH and is
    not inserted into the LIST_GROUP membership.
    """

    version: str = CONTENT_REGION_SEGMENTER_VERSION

    def __init__(self, policy: ContentRegionPolicy | None = None) -> None:
        self._policy = policy if policy is not None else ContentRegionPolicy()

    def segment(
        self,
        elements: Sequence[Element],
        hierarchy_result: HierarchyResult,
        grouping_result: GroupingResult | None = None,
    ) -> ContentRegionSegmentationResult:
        snapshot = tuple(elements)
        self._validate_inputs(snapshot, hierarchy_result, grouping_result)
        routing_categories, grouping_overrides = self._routing_categories(snapshot, grouping_result)
        drafts = self._segment_drafts(snapshot, routing_categories)
        regions = tuple(
            self._make_region(
                index,
                draft,
                hierarchy_result,
                grouping_overrides,
            )
            for index, draft in enumerate(drafts)
        )
        structure, mixed, diagnostics = self._document_structure(
            snapshot,
            regions,
            hierarchy_result.structure,
            routing_categories,
        )
        diagnostics.update(
            {
                "grouping_routing_override_count": len(grouping_overrides),
                "grouping_routing_override_element_ids": sorted(grouping_overrides),
                "grouping_routing_override_unit_types": sorted({str(item["unit_type"]) for item in grouping_overrides.values()}),
                "list_bridge_override_count": sum(item.get("routing_role") == "list_bridge_blank_paragraph" for item in grouping_overrides.values()),
            }
        )
        return ContentRegionSegmentationResult(
            element_count=len(snapshot),
            policy=self._policy,
            regions=regions,
            structure=structure,
            mixed=mixed,
            diagnostics=diagnostics,
        )

    def _routing_categories(
        self,
        elements: tuple[Element, ...],
        grouping_result: GroupingResult | None,
    ) -> tuple[dict[str, ContentCategory], dict[str, dict[str, object]]]:
        categories = {element.id: content_category_for_element(element) for element in elements}
        if grouping_result is None:
            return categories, {}

        overrides: dict[str, dict[str, object]] = {}
        allowed_source_categories = {
            ContentCategory.NARRATIVE,
            ContentCategory.UNKNOWN,
        }
        for unit in grouping_result.logical_units:
            routing_category = _INTERACTION_UNIT_ROUTING.get(unit.type)
            if routing_category is None:
                continue
            for element_id in unit.element_ids:
                current = categories[element_id]
                if current == routing_category:
                    continue
                if current not in allowed_source_categories:
                    raise ContentRegionSegmentationError(
                        f"interaction logical unit {unit.id!r} ({unit.type.value}) conflicts with observed routing category {current.value!r} for element {element_id!r}"
                    )
                previous = overrides.get(element_id)
                if previous is not None and previous["category"] != routing_category.value:
                    raise ContentRegionSegmentationError(f"element {element_id!r} has conflicting structural routing evidence: {previous['category']!r} vs {routing_category.value!r}")
                categories[element_id] = routing_category
                overrides[element_id] = {
                    "category": routing_category.value,
                    "unit_id": unit.id,
                    "unit_type": unit.type.value,
                    "source_category": current.value,
                }

        self._apply_list_bridge_overrides(
            elements,
            grouping_result,
            categories,
            overrides,
        )
        return categories, overrides

    @staticmethod
    def _apply_list_bridge_overrides(
        elements: tuple[Element, ...],
        grouping_result: GroupingResult,
        categories: dict[str, ContentCategory],
        overrides: dict[str, dict[str, object]],
    ) -> None:
        """Route only proven blank list-continuity spacers as derived LIST material."""

        position = {element.id: index for index, element in enumerate(elements)}
        for unit in grouping_result.logical_units:
            if unit.type != LogicalUnitType.LIST_GROUP:
                continue
            if unit.metadata.get("grouping_rule") != "source_native_list_continuity":
                continue
            member_positions = sorted(position[element_id] for element_id in unit.element_ids)
            if len(member_positions) < 2:
                continue
            member_ids = set(unit.element_ids)
            for index in range(member_positions[0] + 1, member_positions[-1]):
                element = elements[index]
                if element.id in member_ids:
                    continue
                if element.type != ElementType.PARAGRAPH:
                    continue
                text = element.text
                if text is not None and text.strip():
                    continue
                current = categories[element.id]
                if current not in {ContentCategory.NARRATIVE, ContentCategory.UNKNOWN}:
                    raise ContentRegionSegmentationError(f"list continuity unit {unit.id!r} crosses non-text routing category {current.value!r} at element {element.id!r}")
                previous = overrides.get(element.id)
                if previous is not None and previous["category"] != ContentCategory.LIST.value:
                    raise ContentRegionSegmentationError(f"blank list bridge {element.id!r} has conflicting structural routing evidence: {previous['category']!r} vs 'list'")
                categories[element.id] = ContentCategory.LIST
                overrides[element.id] = {
                    "category": ContentCategory.LIST.value,
                    "unit_id": unit.id,
                    "unit_type": unit.type.value,
                    "source_category": current.value,
                    "routing_role": "list_bridge_blank_paragraph",
                }

    def _segment_drafts(
        self,
        elements: tuple[Element, ...],
        routing_categories: dict[str, ContentCategory],
    ) -> tuple[_RegionDraft, ...]:
        bridge_categories = set(self._policy.bridge_categories)
        drafts: list[_RegionDraft] = []
        current: _RegionDraft | None = None
        leading_bridges: list[Element] = []

        for element in elements:
            category = routing_categories[element.id]
            if category in bridge_categories:
                if current is None:
                    leading_bridges.append(element)
                else:
                    current.elements.append(element)
                    current.bridge_element_ids.append(element.id)
                continue

            if current is None:
                members = [*leading_bridges, element]
                current = _RegionDraft(
                    routing_category=category,
                    elements=members,
                    bridge_element_ids=[item.id for item in leading_bridges],
                )
                leading_bridges = []
                continue

            if category == current.routing_category:
                current.elements.append(element)
                continue

            drafts.append(current)
            current = _RegionDraft(
                routing_category=category,
                elements=[element],
                bridge_element_ids=[],
            )

        if current is not None:
            if leading_bridges:  # pragma: no cover - consumed once material exists.
                current.elements.extend(leading_bridges)
                current.bridge_element_ids.extend(item.id for item in leading_bridges)
            drafts.append(current)
        else:
            categories = [routing_categories[element.id] for element in leading_bridges]
            counts = Counter(categories)
            highest = max(counts.values())
            routing_category = next(category for category in categories if counts[category] == highest)
            drafts.append(
                _RegionDraft(
                    routing_category=routing_category,
                    elements=list(leading_bridges),
                    bridge_element_ids=[element.id for element in leading_bridges],
                )
            )

        return tuple(drafts)

    def _make_region(
        self,
        index: int,
        draft: _RegionDraft,
        hierarchy_result: HierarchyResult,
        grouping_overrides: dict[str, dict[str, object]],
    ) -> ContentRegion:
        elements = tuple(draft.elements)
        observed_categories = tuple(content_category_for_element(element) for element in elements)
        counts = Counter(observed_categories)
        total = len(elements)
        profile = {category.value: counts[category] / total for category in ContentCategory}
        highest = max(counts.values())
        dominant_candidates = [category for category, count in counts.items() if count == highest]
        dominant = draft.routing_category if draft.routing_category in dominant_candidates else dominant_candidates[0]
        confidence = self._region_confidence(elements)
        structure = self._region_structure(
            tuple(element.id for element in elements),
            draft.routing_category,
            confidence,
            hierarchy_result,
        )
        identity = "|".join(
            (
                self.version,
                str(index),
                draft.routing_category.value,
                *(element.id for element in elements),
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        region_override_ids = [element.id for element in elements if element.id in grouping_overrides]
        return ContentRegion(
            id=f"region_{digest}",
            element_ids=tuple(element.id for element in elements),
            dominant_type=dominant.value,
            profile=profile,
            structure=structure,
            source=StructureSource.DERIVED,
            confidence=confidence,
            metadata={
                "segmenter_version": self.version,
                "policy_version": self._policy.version,
                "routing_category": draft.routing_category.value,
                "start_order": elements[0].order,
                "end_order": elements[-1].order,
                "bridge_element_ids": list(draft.bridge_element_ids),
                "grouping_routing_override_element_ids": region_override_ids,
                "grouping_routing_overrides": {element_id: grouping_overrides[element_id] for element_id in region_override_ids},
                "segmentation_basis": ("contiguous_content_category_plus_structural_grouping" if region_override_ids else "contiguous_content_category"),
                "token_target_used": False,
                "confidence_policy": "uncalibrated_baseline_capped_by_upstream",
            },
        )

    def _region_structure(
        self,
        element_ids: tuple[str, ...],
        routing_category: ContentCategory,
        region_confidence: float,
        hierarchy_result: HierarchyResult,
    ) -> DocumentStructure:
        element_set = set(element_ids)
        nodes = tuple(node for node in hierarchy_result.context_nodes if node.attributes.get("anchor_element_id") in element_set)
        if nodes:
            node_ids = {node.id for node in nodes}
            nested = any(node.parent_id in node_ids for node in nodes)
            if len(nodes) == 1:
                mode = StructureMode.LOCAL
            elif nested:
                mode = StructureMode.HIERARCHICAL
            else:
                mode = StructureMode.GROUPED
            confidence = min(region_confidence, *(node.confidence for node in nodes))
            return DocumentStructure(
                mode=mode,
                source=StructureSource.DERIVED,
                confidence=confidence,
                signals={
                    "anchored_context_ratio": min(1.0, len(nodes) / len(element_ids)),
                    "nested_context_ratio": (sum(node.parent_id in node_ids for node in nodes) / len(nodes)),
                },
            )

        if routing_category in set(self._policy.specialized_local_categories):
            return DocumentStructure(
                mode=StructureMode.LOCAL,
                source=StructureSource.DERIVED,
                confidence=min(
                    region_confidence,
                    self._policy.local_structure_confidence,
                ),
                signals={"specialized_local_pattern": 1.0},
            )
        return DocumentStructure()

    def _document_structure(
        self,
        elements: tuple[Element, ...],
        regions: tuple[ContentRegion, ...],
        global_structure: DocumentStructure,
        routing_categories: dict[str, ContentCategory],
    ) -> tuple[DocumentStructure, bool, dict[str, object]]:
        bridge_categories = set(self._policy.bridge_categories)
        material_categories = [
            routing_categories[element.id] for element in elements if routing_categories[element.id] not in bridge_categories and routing_categories[element.id] != ContentCategory.UNKNOWN
        ]
        distinct = set(material_categories)
        interaction = distinct & set(self._policy.interaction_categories)
        non_narrative_count = sum(category != ContentCategory.NARRATIVE for category in material_categories)
        non_narrative_share = non_narrative_count / len(material_categories) if material_categories else 0.0

        mixed = False
        mixed_reason = "single_or_unknown_pattern"
        if len(distinct) > 1 and interaction:
            mixed = True
            mixed_reason = "interaction_pattern_coexists_with_other_content"
        elif len(distinct) > 1:
            only_hierarchy_embeddable = distinct.issubset(
                {
                    ContentCategory.NARRATIVE,
                    *self._policy.hierarchy_embeddable_categories,
                }
            )
            if global_structure.mode in {StructureMode.HIERARCHICAL, StructureMode.GROUPED} and only_hierarchy_embeddable:
                mixed_reason = "embedded_blocks_preserve_global_hierarchy"
            elif non_narrative_share >= self._policy.embedded_mixed_min_share:
                mixed = True
                mixed_reason = "material_multi_category_share"
            else:
                mixed_reason = "incidental_embedded_content"

        diagnostics: dict[str, object] = {
            "region_count": len(regions),
            "material_category_count": len(distinct),
            "material_categories": sorted(category.value for category in distinct),
            "interaction_categories": sorted(category.value for category in interaction),
            "non_narrative_share": non_narrative_share,
            "mixed_reason": mixed_reason,
        }

        if mixed:
            region_confidences = [region.confidence for region in regions]
            confidence = min(
                self._policy.mixed_structure_confidence,
                *(region_confidences or [self._policy.mixed_structure_confidence]),
            )
            return (
                DocumentStructure(
                    mode=StructureMode.MIXED,
                    source=StructureSource.DERIVED,
                    confidence=confidence,
                    signals={
                        "non_narrative_share": non_narrative_share,
                        "region_density": min(1.0, len(regions) / len(elements)),
                        "material_category_diversity": min(
                            1.0,
                            len(distinct)
                            / max(
                                1,
                                len(ContentCategory) - len(bridge_categories),
                            ),
                        ),
                    },
                ),
                True,
                diagnostics,
            )

        if global_structure.mode != StructureMode.UNKNOWN:
            return global_structure, False, diagnostics

        known_region_structures = [region.structure for region in regions if region.structure.mode != StructureMode.UNKNOWN]
        if len(regions) == 1 and len(known_region_structures) == 1:
            return known_region_structures[0], False, diagnostics

        return global_structure, False, diagnostics

    def _region_confidence(self, elements: tuple[Element, ...]) -> float:
        upstream: list[float] = []
        for element in elements:
            if element.confidence.type is not None:
                upstream.append(element.confidence.type)
            elif element.provenance.confidence is not None:
                upstream.append(element.provenance.confidence)
        if upstream:
            return min(self._policy.region_confidence, *upstream)
        return self._policy.region_confidence

    @staticmethod
    def _validate_inputs(
        elements: tuple[Element, ...],
        hierarchy_result: HierarchyResult,
        grouping_result: GroupingResult | None,
    ) -> None:
        if not elements:
            raise ContentRegionSegmentationError("cannot segment an empty element sequence")
        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise ContentRegionSegmentationError("content region segmentation requires unique ids")
        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise ContentRegionSegmentationError("content region segmentation requires unique element order values")
        if orders != sorted(orders):
            raise ContentRegionSegmentationError("content region segmentation requires ascending canonical source order")
        if hierarchy_result.element_count != len(elements):
            raise ContentRegionSegmentationError("hierarchy element_count does not match content region input")
        assignment_ids = [assignment.element_id for assignment in hierarchy_result.assignments]
        if assignment_ids != ids:
            raise ContentRegionSegmentationError("hierarchy assignments must follow exact canonical element order")

        if grouping_result is None:
            return
        if grouping_result.element_count != len(elements):
            raise ContentRegionSegmentationError("grouping element_count does not match content region input")
        valid_ids = set(ids)
        for unit in grouping_result.logical_units:
            missing = set(unit.element_ids) - valid_ids
            if missing:
                raise ContentRegionSegmentationError(f"logical unit {unit.id!r} references unknown elements for region segmentation: {sorted(missing)}")
