from __future__ import annotations

import hashlib
import itertools
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import Confidence, JsonObject, SchemaModel, StructureSource
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.schemas.logical_unit import LogicalUnit, LogicalUnitType
from rag.source_understanding.source_attributes import (
    INTEGRITY_GROUP_ID_ATTRIBUTE,
    INTEGRITY_PARENT_GROUP_ID_ATTRIBUTE,
    SourceAttributeError,
    source_integrity_group_id,
    source_integrity_parent_group_id,
    source_numbering_format,
    source_numbering_level,
    source_numbering_sequence_id,
    source_zone,
)

from .grouping import GroupingResult

if TYPE_CHECKING:
    from .boundary import BoundarySet


INTEGRITY_CONSOLIDATION_VERSION = "4"
INTEGRITY_CONSOLIDATION_POLICY_VERSION = "4"


class IntegrityConsolidationError(ValueError):
    """Existing grouping cannot be consolidated without changing trusted membership."""


@dataclass(frozen=True, slots=True)
class _Family:
    name: str
    element_types: frozenset[ElementType]
    unit_type: LogicalUnitType
    container_types: frozenset[ElementType] = frozenset()


@dataclass(frozen=True, slots=True)
class _IntegritySpan:
    family: _Family
    member_ids: tuple[str, ...]
    boundary_ids: tuple[str, ...]
    boundary_classes: tuple[str, ...]
    native_group_id: str | None = None
    native_parent_group_id: str | None = None
    native_group_ids: tuple[str, ...] = ()


_FAMILIES = (
    _Family(
        "table",
        frozenset({ElementType.TABLE, ElementType.TABLE_ROW, ElementType.TABLE_CELL}),
        LogicalUnitType.TABLE_BLOCK,
        frozenset({ElementType.TABLE}),
    ),
    _Family(
        "list",
        frozenset({ElementType.LIST, ElementType.LIST_ITEM}),
        LogicalUnitType.LIST_GROUP,
        frozenset({ElementType.LIST}),
    ),
    _Family("code", frozenset({ElementType.CODE}), LogicalUnitType.CODE_BLOCK),
    _Family("formula", frozenset({ElementType.FORMULA}), LogicalUnitType.TEXT_BLOCK),
    _Family("key_value", frozenset({ElementType.KEY_VALUE}), LogicalUnitType.KEY_VALUE_GROUP),
)
_TYPE_TO_FAMILY = {element_type: family for family in _FAMILIES for element_type in family.element_types}


def native_integrity_group_id(element: Element) -> str | None:
    try:
        return source_integrity_group_id(element)
    except SourceAttributeError as exc:
        raise IntegrityConsolidationError(str(exc)) from exc


def native_integrity_parent_group_id(element: Element) -> str | None:
    try:
        return source_integrity_parent_group_id(element)
    except SourceAttributeError as exc:
        raise IntegrityConsolidationError(str(exc)) from exc


class IntegrityConsolidationPolicy(SchemaModel):
    version: str = INTEGRITY_CONSOLIDATION_POLICY_VERSION
    confidence: Confidence = 0.92
    merge_across_soft_boundaries: bool = True
    merge_across_unknown_boundaries: bool = True
    split_on_repeated_container: bool = True
    prefer_native_group_identity: bool = True
    require_native_parent_present: bool = True
    merge_contiguous_native_list_fragments: bool = True
    merge_native_list_fragments_across_blank_spacers: bool = True
    merge_native_list_fragments_across_section_breaks: bool = True
    max_native_list_blank_spacers: int = Field(default=2, ge=0, le=8)
    list_indentation_tolerance: float = Field(default=1.0, ge=0.0, le=100.0)


class IntegrityConsolidationReport(SchemaModel):
    version: str = INTEGRITY_CONSOLIDATION_VERSION
    element_count: int = Field(ge=1)
    policy: IntegrityConsolidationPolicy
    consolidated_unit_ids: tuple[str, ...] = Field(default_factory=tuple)
    family_counts: JsonObject = Field(default_factory=dict)
    replaced_unit_ids: tuple[str, ...] = Field(default_factory=tuple)
    native_group_count: int = Field(default=0, ge=0)
    nested_native_group_count: int = Field(default=0, ge=0)
    merged_native_list_group_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_ids(self) -> IntegrityConsolidationReport:
        if len(self.consolidated_unit_ids) != len(set(self.consolidated_unit_ids)):
            raise ValueError("consolidated_unit_ids must be unique")
        if len(self.replaced_unit_ids) != len(set(self.replaced_unit_ids)):
            raise ValueError("replaced_unit_ids must be unique")
        return self


def unresolved_integrity_boundary_ids(
    boundary_set: BoundarySet,
    grouping_result: GroupingResult,
) -> tuple[str, ...]:
    """Return integrity-warning boundaries not resolved by structural ownership.

    A warning is resolved when both sides are in one LogicalUnit *or* both sides
    belong to distinct source-native integrity groups. In the latter case the
    source has explicitly told us there is a block boundary; merging is not the
    resolution, preserving the distinct blocks is.
    """

    owners: dict[str, str] = {}
    unit_by_id = {unit.id: unit for unit in grouping_result.logical_units}
    for unit in grouping_result.logical_units:
        for element_id in unit.element_ids:
            previous = owners.get(element_id)
            if previous is not None and previous != unit.id:
                raise IntegrityConsolidationError(f"element {element_id!r} belongs to multiple logical units")
            owners[element_id] = unit.id

    unresolved: list[str] = []
    for boundary in boundary_set.boundaries:
        reason_values = {getattr(reason, "value", str(reason)) for reason in getattr(boundary, "reasons", ())}
        if "CONTENT_INTEGRITY_UNRESOLVED" not in reason_values:
            continue
        left_owner = owners.get(boundary.left_element_id)
        right_owner = owners.get(boundary.right_element_id)
        if left_owner is not None and left_owner == right_owner:
            continue
        left_group = unit_by_id[left_owner].metadata.get(INTEGRITY_GROUP_ID_ATTRIBUTE) if left_owner in unit_by_id else None
        right_group = unit_by_id[right_owner].metadata.get(INTEGRITY_GROUP_ID_ATTRIBUTE) if right_owner in unit_by_id else None
        if isinstance(left_group, str) and isinstance(right_group, str):
            # Distinct native groups are an explicit resolution of the adjacency.
            continue
        unresolved.append(boundary.id)
    return tuple(unresolved)


class IntegrityGroupConsolidator:
    """Preserve native integrity while reconciling conservative visual list continuity."""

    version = INTEGRITY_CONSOLIDATION_VERSION

    def __init__(self, policy: IntegrityConsolidationPolicy | None = None) -> None:
        self._policy = policy if policy is not None else IntegrityConsolidationPolicy()

    @property
    def policy(self) -> IntegrityConsolidationPolicy:
        return self._policy

    def consolidate(
        self,
        elements: Sequence[Element],
        boundary_set: BoundarySet,
        grouping_result: GroupingResult,
    ) -> tuple[GroupingResult, IntegrityConsolidationReport]:
        snapshot = tuple(elements)
        self._validate_inputs(snapshot, boundary_set, grouping_result)
        spans = self._integrity_spans(snapshot, boundary_set)
        if not spans:
            return grouping_result, IntegrityConsolidationReport(
                element_count=len(snapshot),
                policy=self._policy,
            )

        by_id = {element.id: element for element in snapshot}
        order = {element.id: index for index, element in enumerate(snapshot)}
        existing = list(grouping_result.logical_units)
        replaced: list[str] = []
        created: list[LogicalUnit] = []
        family_counts: Counter[str] = Counter()
        native_count = 0
        nested_count = 0
        merged_native_list_count = 0

        for span in spans:
            member_set = set(span.member_ids)
            overlapping = [unit for unit in existing if member_set.intersection(unit.element_ids)]
            for unit in overlapping:
                outside = set(unit.element_ids) - member_set
                if outside:
                    raise IntegrityConsolidationError(f"logical unit {unit.id!r} crosses {span.family.name!r} integrity span")
            if overlapping:
                replaced.extend(unit.id for unit in overlapping)
                existing = [unit for unit in existing if unit not in overlapping]

            created.append(self._make_unit(span, by_id, overlapping))
            family_counts[span.family.name] += 1
            if span.native_group_ids or span.native_group_id is not None:
                native_count += 1
            if span.native_parent_group_id is not None:
                nested_count += 1
            if span.family.name == "list" and len(span.native_group_ids) > 1:
                merged_native_list_count += len(span.native_group_ids) - 1

        units = [*existing, *created]
        units.sort(key=lambda unit: order[unit.element_ids[0]])
        consolidated_members = {element_id for unit in created for element_id in unit.element_ids}
        ungrouped = tuple(element_id for element_id in grouping_result.ungrouped_element_ids if element_id not in consolidated_members)
        data = grouping_result.model_dump(mode="python")
        data["logical_units"] = tuple(units)
        data["ungrouped_element_ids"] = ungrouped
        consolidated = GroupingResult.model_validate(data)
        report = IntegrityConsolidationReport(
            element_count=len(snapshot),
            policy=self._policy,
            consolidated_unit_ids=tuple(unit.id for unit in created),
            family_counts=dict(sorted(family_counts.items())),
            replaced_unit_ids=tuple(dict.fromkeys(replaced)),
            native_group_count=native_count,
            nested_native_group_count=nested_count,
            merged_native_list_group_count=merged_native_list_count,
        )
        return consolidated, report

    def _integrity_spans(
        self,
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
    ) -> tuple[_IntegritySpan, ...]:
        native_spans, keyed_ids = self._native_spans(elements, boundary_set)
        native_spans = self._coalesce_native_list_spans(native_spans, elements, boundary_set)
        heuristic_spans = self._heuristic_spans(elements, boundary_set, keyed_ids)
        position = {element.id: index for index, element in enumerate(elements)}
        return tuple(
            sorted(
                (*native_spans, *heuristic_spans),
                key=lambda span: position[span.member_ids[0]],
            )
        )

    def _native_spans(
        self,
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
    ) -> tuple[tuple[_IntegritySpan, ...], set[str]]:
        if not self._policy.prefer_native_group_identity:
            return (), set()
        position = {element.id: index for index, element in enumerate(elements)}
        groups: dict[str, list[Element]] = {}
        families: dict[str, _Family] = {}
        parents: dict[str, str | None] = {}
        keyed_ids: set[str] = set()
        for element in elements:
            family = _TYPE_TO_FAMILY.get(element.type)
            if family is None:
                continue
            group_id = native_integrity_group_id(element)
            if group_id is None:
                continue
            parent_id = native_integrity_parent_group_id(element)
            previous_family = families.get(group_id)
            if previous_family is not None and previous_family != family:
                raise IntegrityConsolidationError(f"native integrity group {group_id!r} mixes incompatible families")
            previous_parent = parents.get(group_id)
            if group_id in parents and previous_parent != parent_id:
                raise IntegrityConsolidationError(f"native integrity group {group_id!r} carries inconsistent parent ids")
            families[group_id] = family
            parents[group_id] = parent_id
            groups.setdefault(group_id, []).append(element)
            keyed_ids.add(element.id)

        if self._policy.require_native_parent_present:
            missing = sorted({parent for parent in parents.values() if parent is not None and parent not in groups})
            if missing:
                raise IntegrityConsolidationError(f"native integrity parent groups are missing: {missing}")

        for group_id in groups:
            seen: set[str] = set()
            current: str | None = group_id
            while current is not None:
                if current in seen:
                    raise IntegrityConsolidationError(f"native integrity parent hierarchy contains cycle at {current!r}")
                seen.add(current)
                current = parents.get(current)

        def is_descendant(candidate_group: str, ancestor_group: str) -> bool:
            seen: set[str] = set()
            current: str | None = candidate_group
            while current is not None:
                if current in seen:
                    return False
                seen.add(current)
                current = parents.get(current)
                if current == ancestor_group:
                    return True
            return False

        element_group = {element.id: native_integrity_group_id(element) for element in elements}
        for group_id, members in groups.items():
            member_positions = sorted(position[element.id] for element in members)
            for left_pos, right_pos in itertools.pairwise(member_positions):
                if right_pos <= left_pos + 1:
                    continue
                for candidate in elements[left_pos + 1 : right_pos]:
                    candidate_group = element_group.get(candidate.id)
                    if candidate_group is None or candidate_group not in groups or not is_descendant(candidate_group, group_id):
                        raise IntegrityConsolidationError(f"native integrity group {group_id!r} crosses unrelated element {candidate.id!r}")

        spans: list[_IntegritySpan] = []
        for group_id, members in groups.items():
            members.sort(key=lambda element: position[element.id])
            boundary_ids: list[str] = []
            boundary_classes: list[str] = []
            for left, right in itertools.pairwise(members):
                left_pos = position[left.id]
                right_pos = position[right.id]
                if right_pos == left_pos + 1:
                    boundary = boundary_set.boundaries[left_pos]
                    value = getattr(boundary.classification, "value", str(boundary.classification))
                    if value == "HARD":
                        raise IntegrityConsolidationError(f"native integrity group {group_id!r} crosses a HARD boundary {boundary.id!r}")
                    boundary_ids.append(boundary.id)
                    boundary_classes.append(value)
            spans.append(
                _IntegritySpan(
                    family=families[group_id],
                    member_ids=tuple(element.id for element in members),
                    boundary_ids=tuple(boundary_ids),
                    boundary_classes=tuple(boundary_classes),
                    native_group_id=group_id,
                    native_parent_group_id=parents[group_id],
                    native_group_ids=(group_id,),
                )
            )
        return tuple(spans), keyed_ids

    def _coalesce_native_list_spans(
        self,
        spans: tuple[_IntegritySpan, ...],
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
    ) -> tuple[_IntegritySpan, ...]:
        if not self._policy.merge_contiguous_native_list_fragments:
            return spans
        position = {element.id: index for index, element in enumerate(elements)}
        ordered = sorted(spans, key=lambda span: position[span.member_ids[0]])
        output: list[_IntegritySpan] = []
        for span in ordered:
            if not output:
                output.append(span)
                continue
            previous = output[-1]
            if not self._can_merge_native_list_spans(previous, span, elements, boundary_set, position):
                output.append(span)
                continue
            bridge_ids, bridge_classes = self._bridge_boundaries(previous, span, boundary_set, position)
            native_ids = tuple(
                dict.fromkeys(
                    (
                        *(previous.native_group_ids or (() if previous.native_group_id is None else (previous.native_group_id,))),
                        *(span.native_group_ids or (() if span.native_group_id is None else (span.native_group_id,))),
                    )
                )
            )
            parent_id = previous.native_parent_group_id if previous.native_parent_group_id == span.native_parent_group_id else None
            output[-1] = _IntegritySpan(
                family=previous.family,
                member_ids=(*previous.member_ids, *span.member_ids),
                boundary_ids=(*previous.boundary_ids, *bridge_ids, *span.boundary_ids),
                boundary_classes=(
                    *previous.boundary_classes,
                    *bridge_classes,
                    *span.boundary_classes,
                ),
                native_group_id=native_ids[0] if len(native_ids) == 1 else None,
                native_parent_group_id=parent_id,
                native_group_ids=native_ids,
            )
        return tuple(output)

    def _can_merge_native_list_spans(
        self,
        left: _IntegritySpan,
        right: _IntegritySpan,
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
        position: dict[str, int],
    ) -> bool:
        if left.family.name != "list" or right.family.name != "list":
            return False
        left_pos = position[left.member_ids[-1]]
        right_pos = position[right.member_ids[0]]
        if right_pos <= left_pos:
            return False
        left_element = elements[left_pos]
        right_element = elements[right_pos]
        if not self._same_story(left_element, right_element):
            return False

        gap = elements[left_pos + 1 : right_pos]
        if self._is_section_break_list_bridge(left_element, right_element, gap):
            return True

        boundaries = boundary_set.boundaries[left_pos:right_pos]
        if any(not self._can_cross(boundary.classification) for boundary in boundaries):
            return False
        if not gap:
            return True
        if not self._policy.merge_native_list_fragments_across_blank_spacers:
            return False
        if len(gap) > self._policy.max_native_list_blank_spacers:
            return False
        if any(element.type != ElementType.PARAGRAPH or (element.text is not None and element.text.strip()) for element in gap):
            return False
        return self._compatible_list_edges(left_element, right_element)

    def _is_section_break_list_bridge(
        self,
        left: Element,
        right: Element,
        gap: tuple[Element, ...],
    ) -> bool:
        if not self._policy.merge_native_list_fragments_across_section_breaks:
            return False
        if len(gap) != 1:
            return False
        separator = gap[0]
        if (element_type := separator.type) and element_type != ElementType.SEPARATOR:
            return False
        if separator.attributes.get("separator_kind") != "section_break":
            return False
        try:
            left_sequence = source_numbering_sequence_id(left)
            right_sequence = source_numbering_sequence_id(right)
            left_format = source_numbering_format(left)
            right_format = source_numbering_format(right)
        except SourceAttributeError as exc:
            raise IntegrityConsolidationError(str(exc)) from exc
        if left_sequence is None or right_sequence is None or left_sequence != right_sequence:
            return False
        if left_format is None or right_format is None:
            return False
        return left_format.casefold() == right_format.casefold()

    def _compatible_list_edges(self, left: Element, right: Element) -> bool:
        try:
            left_level = source_numbering_level(left)
            right_level = source_numbering_level(right)
            left_format = source_numbering_format(left)
            right_format = source_numbering_format(right)
        except SourceAttributeError as exc:
            raise IntegrityConsolidationError(str(exc)) from exc
        if left_level is None or right_level is None or left_level != right_level:
            return False
        if left_format is None or right_format is None or left_format.casefold() != right_format.casefold():
            return False
        left_indent = None if left.style is None else left.style.indentation
        right_indent = None if right.style is None else right.style.indentation
        if left_indent is None or right_indent is None:
            return left_indent is None and right_indent is None
        return abs(left_indent - right_indent) <= self._policy.list_indentation_tolerance

    @staticmethod
    def _same_story(left: Element, right: Element) -> bool:
        left_part = left.attributes.get("opc_part")
        right_part = right.attributes.get("opc_part")
        if left_part != right_part:
            return False
        try:
            return source_zone(left) == source_zone(right)
        except SourceAttributeError as exc:
            raise IntegrityConsolidationError(str(exc)) from exc

    @staticmethod
    def _bridge_boundaries(
        left: _IntegritySpan,
        right: _IntegritySpan,
        boundary_set: BoundarySet,
        position: dict[str, int],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        left_pos = position[left.member_ids[-1]]
        right_pos = position[right.member_ids[0]]
        bridge = boundary_set.boundaries[left_pos:right_pos]
        return (
            tuple(boundary.id for boundary in bridge),
            tuple(getattr(boundary.classification, "value", str(boundary.classification)) for boundary in bridge),
        )

    def _heuristic_spans(
        self,
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
        keyed_ids: set[str],
    ) -> tuple[_IntegritySpan, ...]:
        spans: list[_IntegritySpan] = []
        index = 0
        while index < len(elements):
            element = elements[index]
            family = _TYPE_TO_FAMILY.get(element.type)
            if family is None or element.id in keyed_ids:
                index += 1
                continue
            members = [element.id]
            boundary_ids: list[str] = []
            boundary_classes: list[str] = []
            seen_container = element.type in family.container_types
            cursor = index + 1
            while cursor < len(elements):
                candidate = elements[cursor]
                if candidate.id in keyed_ids or candidate.type not in family.element_types:
                    break
                boundary = boundary_set.boundaries[cursor - 1]
                if not self._can_cross(boundary.classification):
                    break
                is_container = candidate.type in family.container_types
                if self._policy.split_on_repeated_container and is_container and seen_container:
                    break
                seen_container = seen_container or is_container
                members.append(candidate.id)
                boundary_ids.append(boundary.id)
                boundary_classes.append(getattr(boundary.classification, "value", str(boundary.classification)))
                cursor += 1
            spans.append(
                _IntegritySpan(
                    family=family,
                    member_ids=tuple(members),
                    boundary_ids=tuple(boundary_ids),
                    boundary_classes=tuple(boundary_classes),
                )
            )
            index = cursor
        return tuple(spans)

    def _can_cross(self, boundary_class: Any) -> bool:
        value = getattr(boundary_class, "value", str(boundary_class))
        if value == "HARD":
            return False
        if value == "SOFT":
            return self._policy.merge_across_soft_boundaries
        if value == "UNKNOWN":
            return self._policy.merge_across_unknown_boundaries
        return True

    def _make_unit(
        self,
        span: _IntegritySpan,
        by_id: dict[str, Element],
        replaced_units: list[LogicalUnit],
    ) -> LogicalUnit:
        confidences = [self._policy.confidence]
        confidences.extend(unit.confidence for unit in replaced_units)
        for element_id in span.member_ids:
            element = by_id[element_id]
            if element.confidence.type is not None:
                confidences.append(element.confidence.type)
            elif element.provenance.confidence is not None:
                confidences.append(element.provenance.confidence)
        native_ids = span.native_group_ids or (() if span.native_group_id is None else (span.native_group_id,))
        identity_parts = [span.family.name, span.family.unit_type.value, *span.member_ids]
        if native_ids:
            identity_parts.extend(("native", *native_ids))
        digest = hashlib.sha256("|".join(identity_parts).encode("utf-8")).hexdigest()[:20]
        if len(native_ids) > 1 and span.family.name == "list":
            grouping_rule = "source_native_list_continuity"
        elif native_ids:
            grouping_rule = "source_native_integrity_group"
        else:
            grouping_rule = "contiguous_integrity_family"
        metadata: dict[str, object] = {
            "grouping_rule": grouping_rule,
            "integrity_family": span.family.name,
            "replaced_unit_ids": [unit.id for unit in replaced_units],
            "boundary_ids": list(span.boundary_ids),
            "boundary_classes": list(span.boundary_classes),
            "token_target_used": False,
            "confidence_policy": "uncalibrated_baseline_capped_by_upstream",
        }
        if len(native_ids) == 1:
            metadata[INTEGRITY_GROUP_ID_ATTRIBUTE] = native_ids[0]
        elif native_ids:
            metadata["source_native_integrity_group_ids"] = list(native_ids)
        if span.native_parent_group_id is not None:
            metadata[INTEGRITY_PARENT_GROUP_ID_ATTRIBUTE] = span.native_parent_group_id
        return LogicalUnit(
            id=f"lu_{digest}",
            type=span.family.unit_type,
            element_ids=span.member_ids,
            source=StructureSource.DERIVED,
            confidence=min(confidences),
            metadata=metadata,
        )

    @staticmethod
    def _validate_inputs(
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
        grouping_result: GroupingResult,
    ) -> None:
        if not elements:
            raise IntegrityConsolidationError("cannot consolidate an empty element sequence")
        if boundary_set.element_count != len(elements):
            raise IntegrityConsolidationError("boundary element_count does not match integrity input")
        if grouping_result.element_count != len(elements):
            raise IntegrityConsolidationError("grouping element_count does not match integrity input")
        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise IntegrityConsolidationError("integrity consolidation requires unique element ids")
        if len(boundary_set.boundaries) != max(0, len(elements) - 1):
            raise IntegrityConsolidationError("boundary count does not match canonical adjacency")
        for index, boundary in enumerate(boundary_set.boundaries):
            if boundary.left_element_id != elements[index].id or boundary.right_element_id != elements[index + 1].id:
                raise IntegrityConsolidationError(f"boundary {boundary.id!r} does not match canonical adjacency")
