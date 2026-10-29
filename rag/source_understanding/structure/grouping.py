from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import Confidence, Identifier, SchemaModel, StructureSource
from rag.source_understanding.schemas.document import SubDocument
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.schemas.logical_unit import LogicalUnit, LogicalUnitType

from .boundary import BoundaryClass, BoundarySet
from .dialogue import DialogueSegmentBuilder
from .log import LogWindowBuilder
from .qa import QAPairBuilder, QAPairRule
from .signals import StructureSignalKind, StructureSignalSet
from .subdocument import SubDocumentDetector

GROUPING_VERSION = "2"
GROUPING_POLICY_VERSION = "2"

_PARENTHESIZED_LIST_MARKER_RE = re.compile(r"^\s*\((?:\d+|[A-Za-z]|[IVXLCDMivxlcdm]+)\)(?=\s|\t)")


class GroupingError(ValueError):
    """Grouping inputs are inconsistent with canonical structure evidence."""


class GroupingPolicy(SchemaModel):
    """Conservative deterministic grouping policy kept with the result."""

    version: str = GROUPING_POLICY_VERSION
    explicit_qa_confidence: Confidence = 0.95
    lexical_qa_confidence: Confidence = 0.80
    lexical_list_confidence: Confidence = 0.85
    dialogue_confidence: Confidence = 0.90
    log_confidence: Confidence = 0.90
    atomic_structured_confidence: Confidence = 0.95
    text_block_confidence: Confidence = 0.70
    unknown_group_confidence: Confidence = 0.50
    subdocument_confidence: Confidence = 0.85
    min_dialogue_turns: int = Field(default=2, ge=2)
    min_log_entries: int = Field(default=2, ge=2)
    min_lexical_list_markers: int = Field(default=2, ge=2, le=32)
    max_lexical_list_blank_bridges: int = Field(default=1, ge=0, le=4)
    merge_soft_boundaries: bool = True


class GroupingResult(SchemaModel):
    version: str = GROUPING_VERSION
    element_count: int = Field(ge=1)
    signal_version: str
    boundary_version: str
    policy: GroupingPolicy
    logical_units: tuple[LogicalUnit, ...] = Field(default_factory=tuple)
    subdocuments: tuple[SubDocument, ...] = Field(default_factory=tuple)
    ungrouped_element_ids: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_result(self) -> GroupingResult:
        unit_ids = [unit.id for unit in self.logical_units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("grouping result logical unit ids must be unique")
        subdoc_ids = [subdoc.id for subdoc in self.subdocuments]
        if len(subdoc_ids) != len(set(subdoc_ids)):
            raise ValueError("grouping result subdocument ids must be unique")

        owners: dict[str, str] = {}
        for unit in self.logical_units:
            for element_id in unit.element_ids:
                previous = owners.get(element_id)
                if previous is not None:
                    raise ValueError(f"element {element_id!r} belongs to multiple grouping-stage logical units: {previous!r} and {unit.id!r}")
                owners[element_id] = unit.id

        if len(self.ungrouped_element_ids) != len(set(self.ungrouped_element_ids)):
            raise ValueError("ungrouped_element_ids must be unique")
        overlap = set(self.ungrouped_element_ids) & owners.keys()
        if overlap:
            raise ValueError(f"grouped elements cannot also be marked ungrouped: {sorted(overlap)}")
        return self


_ATOMIC_UNIT_TYPES: dict[ElementType, LogicalUnitType] = {
    ElementType.TABLE: LogicalUnitType.TABLE_BLOCK,
    ElementType.CODE: LogicalUnitType.CODE_BLOCK,
    ElementType.LIST: LogicalUnitType.LIST_GROUP,
    ElementType.KEY_VALUE: LogicalUnitType.KEY_VALUE_GROUP,
    ElementType.FORMULA: LogicalUnitType.TEXT_BLOCK,
}

_FALLBACK_TEXT_TYPES = frozenset(
    {
        ElementType.PARAGRAPH,
        ElementType.SENTENCE,
        ElementType.LINE,
        ElementType.CAPTION,
        ElementType.FOOTNOTE,
    }
)


class LogicalGroupBuilder:
    """Build non-overlapping local LogicalUnits without hierarchy or semantics."""

    version: str = GROUPING_VERSION

    def __init__(self, policy: GroupingPolicy | None = None) -> None:
        self._policy = policy if policy is not None else GroupingPolicy()
        self._qa = QAPairBuilder()
        self._dialogue = DialogueSegmentBuilder()
        self._log = LogWindowBuilder()
        self._subdocuments = SubDocumentDetector()

    def build(
        self,
        elements: Sequence[Element],
        signal_set: StructureSignalSet,
        boundary_set: BoundarySet,
    ) -> GroupingResult:
        snapshot = tuple(elements)
        self._validate_inputs(snapshot, signal_set, boundary_set)
        order = {element.id: index for index, element in enumerate(snapshot)}
        by_id = {element.id: element for element in snapshot}

        units: list[LogicalUnit] = []
        consumed: set[str] = set()

        for candidate in self._qa.detect(snapshot, signal_set, boundary_set):
            member_ids = (
                candidate.question_element_id,
                candidate.answer_element_id,
            )
            confidence = self._policy.explicit_qa_confidence if candidate.rule == QAPairRule.EXPLICIT_TYPE else self._policy.lexical_qa_confidence
            units.append(
                self._make_unit(
                    LogicalUnitType.QA_PAIR,
                    member_ids,
                    by_id,
                    confidence=confidence,
                    source=candidate.source,
                    metadata={
                        "grouping_rule": candidate.rule.value,
                        "boundary_id": candidate.boundary_id,
                        "signal_ids": list(candidate.signal_ids),
                    },
                )
            )
            consumed.update(member_ids)

        for candidate in self._dialogue.detect(
            snapshot,
            boundary_set,
            min_turns=self._policy.min_dialogue_turns,
        ):
            if consumed.intersection(candidate.element_ids):
                continue
            units.append(
                self._make_unit(
                    LogicalUnitType.DIALOGUE_SEGMENT,
                    candidate.element_ids,
                    by_id,
                    confidence=self._policy.dialogue_confidence,
                    source=StructureSource.DERIVED,
                    metadata={
                        "grouping_rule": "contiguous_dialogue_turns",
                        "boundary_ids": list(candidate.boundary_ids),
                    },
                )
            )
            consumed.update(candidate.element_ids)

        for candidate in self._log.detect(
            snapshot,
            boundary_set,
            min_entries=self._policy.min_log_entries,
        ):
            if consumed.intersection(candidate.element_ids):
                continue
            units.append(
                self._make_unit(
                    LogicalUnitType.LOG_WINDOW,
                    candidate.element_ids,
                    by_id,
                    confidence=self._policy.log_confidence,
                    source=StructureSource.DERIVED,
                    metadata={
                        "grouping_rule": "contiguous_log_entries",
                        "boundary_ids": list(candidate.boundary_ids),
                    },
                )
            )
            consumed.update(candidate.element_ids)

        self._append_lexical_list_groups(
            snapshot,
            signal_set,
            boundary_set,
            by_id,
            consumed,
            units,
        )

        for element in snapshot:
            if element.id in consumed:
                continue
            unit_type = _ATOMIC_UNIT_TYPES.get(element.type)
            if unit_type is None:
                continue
            units.append(
                self._make_unit(
                    unit_type,
                    (element.id,),
                    by_id,
                    confidence=self._policy.atomic_structured_confidence,
                    source=StructureSource.DERIVED,
                    metadata={"grouping_rule": "atomic_integrity_element"},
                )
            )
            consumed.add(element.id)

        self._append_fallback_text_blocks(
            snapshot,
            boundary_set,
            by_id,
            consumed,
            units,
        )

        for element in snapshot:
            if element.id in consumed or element.type != ElementType.UNKNOWN:
                continue
            units.append(
                self._make_unit(
                    LogicalUnitType.UNKNOWN_GROUP,
                    (element.id,),
                    by_id,
                    confidence=self._policy.unknown_group_confidence,
                    source=StructureSource.DERIVED,
                    metadata={"grouping_rule": "preserve_unknown_element"},
                )
            )
            consumed.add(element.id)

        units.sort(key=lambda unit: order[unit.element_ids[0]])
        ungrouped = tuple(element.id for element in snapshot if element.id not in consumed)
        subdocuments = self._subdocuments.detect(
            snapshot,
            signal_set,
            boundary_set,
            confidence=self._policy.subdocument_confidence,
        )

        return GroupingResult(
            element_count=len(snapshot),
            signal_version=signal_set.version,
            boundary_version=boundary_set.version,
            policy=self._policy,
            logical_units=tuple(units),
            subdocuments=subdocuments,
            ungrouped_element_ids=ungrouped,
        )

    def _append_lexical_list_groups(
        self,
        elements: tuple[Element, ...],
        signal_set: StructureSignalSet,
        boundary_set: BoundarySet,
        by_id: dict[str, Element],
        consumed: set[str],
        units: list[LogicalUnit],
    ) -> None:
        marker_signal_ids: dict[str, list[str]] = {}
        for signal in signal_set.signals:
            if signal.kind != StructureSignalKind.NUMBERING_MARKER:
                continue
            if len(signal.element_ids) != 1:
                continue
            marker_signal_ids.setdefault(signal.element_ids[0], []).append(signal.id)

        def is_marker(element: Element) -> bool:
            if element.type != ElementType.PARAGRAPH or element.id in consumed:
                return False
            if element.id in marker_signal_ids:
                return True
            text = element.text
            return bool(text and _PARENTHESIZED_LIST_MARKER_RE.match(text))

        def is_parenthesized_marker(element: Element) -> bool:
            text = element.text
            return bool(text and _PARENTHESIZED_LIST_MARKER_RE.match(text))

        def has_introducing_clause(first_marker_index: int) -> bool:
            cursor = first_marker_index - 1
            while cursor >= 0:
                candidate = elements[cursor]
                if candidate.type == ElementType.PARAGRAPH and (candidate.text is None or not candidate.text.strip()):
                    cursor -= 1
                    continue
                text = candidate.text
                return bool(candidate.type == ElementType.LIST_ITEM and text and text.rstrip().endswith(":"))
            return False

        def can_cross(boundary_index: int) -> bool:
            boundary = boundary_set.boundaries[boundary_index]
            return boundary.classification not in {
                BoundaryClass.HARD,
                BoundaryClass.UNKNOWN,
            }

        index = 0
        while index < len(elements):
            if not is_marker(elements[index]):
                index += 1
                continue

            member_indices = [index]
            marker_count = 1
            cursor = index + 1
            pending_blanks: list[int] = []
            while cursor < len(elements):
                candidate = elements[cursor]
                if candidate.id in consumed or not can_cross(cursor - 1):
                    break
                if is_marker(candidate):
                    member_indices.extend(pending_blanks)
                    pending_blanks = []
                    member_indices.append(cursor)
                    marker_count += 1
                    cursor += 1
                    continue
                if candidate.type == ElementType.PARAGRAPH and (candidate.text is None or not candidate.text.strip()) and len(pending_blanks) < self._policy.max_lexical_list_blank_bridges:
                    pending_blanks.append(cursor)
                    cursor += 1
                    continue
                break

            if marker_count < self._policy.min_lexical_list_markers:
                index += 1
                continue

            parenthesized = is_parenthesized_marker(elements[index])
            if not parenthesized and not has_introducing_clause(index):
                index += 1
                continue

            member_ids = tuple(elements[item].id for item in member_indices)
            signal_ids = [signal_id for element_id in member_ids for signal_id in marker_signal_ids.get(element_id, ())]
            units.append(
                self._make_unit(
                    LogicalUnitType.LIST_GROUP,
                    member_ids,
                    by_id,
                    confidence=self._policy.lexical_list_confidence,
                    source=StructureSource.INFERRED,
                    metadata={
                        "grouping_rule": "lexical_numbering_sequence",
                        "evidence_rule": ("parenthesized_enumeration" if parenthesized else "introduced_native_list_subsequence"),
                        "signal_ids": signal_ids,
                        "marker_count": marker_count,
                        "blank_bridge_count": sum(elements[item].text is None or not (elements[item].text or "").strip() for item in member_indices),
                    },
                )
            )
            consumed.update(member_ids)
            index = cursor

    def _append_fallback_text_blocks(
        self,
        elements: tuple[Element, ...],
        boundary_set: BoundarySet,
        by_id: dict[str, Element],
        consumed: set[str],
        units: list[LogicalUnit],
    ) -> None:
        start: int | None = None

        def flush(end: int) -> None:
            nonlocal start
            if start is None:
                return
            member_ids = tuple(element.id for element in elements[start:end] if element.id not in consumed and element.type in _FALLBACK_TEXT_TYPES)
            if member_ids:
                units.append(
                    self._make_unit(
                        LogicalUnitType.TEXT_BLOCK,
                        member_ids,
                        by_id,
                        confidence=self._policy.text_block_confidence,
                        source=StructureSource.DERIVED,
                        metadata={"grouping_rule": "boundary_delimited_text"},
                    )
                )
                consumed.update(member_ids)
            start = None

        for index, element in enumerate(elements):
            eligible = element.id not in consumed and element.type in _FALLBACK_TEXT_TYPES
            if not eligible:
                flush(index)
                continue

            if start is None:
                start = index
                continue

            boundary = boundary_set.boundaries[index - 1]
            should_break = boundary.classification in {
                BoundaryClass.HARD,
                BoundaryClass.UNKNOWN,
            } or (boundary.classification == BoundaryClass.SOFT and not self._policy.merge_soft_boundaries)
            if should_break:
                flush(index)
                start = index

        flush(len(elements))

    @staticmethod
    def _make_unit(
        unit_type: LogicalUnitType,
        element_ids: tuple[str, ...],
        by_id: dict[str, Element],
        *,
        confidence: float,
        source: StructureSource,
        metadata: dict[str, object],
    ) -> LogicalUnit:
        upstream_confidences = []
        for element_id in element_ids:
            element = by_id[element_id]
            if element.confidence.type is not None:
                upstream_confidences.append(element.confidence.type)
            elif element.provenance.confidence is not None:
                upstream_confidences.append(element.provenance.confidence)

        effective_confidence = min([confidence, *upstream_confidences] if upstream_confidences else [confidence])
        identity = "|".join((unit_type.value, *element_ids))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return LogicalUnit(
            id=f"lu_{digest}",
            type=unit_type,
            element_ids=element_ids,
            source=source,
            confidence=effective_confidence,
            metadata={
                **metadata,
                "confidence_policy": "uncalibrated_baseline_capped_by_upstream",
            },
        )

    @staticmethod
    def _validate_inputs(
        elements: tuple[Element, ...],
        signal_set: StructureSignalSet,
        boundary_set: BoundarySet,
    ) -> None:
        if not elements:
            raise GroupingError("cannot group an empty element sequence")

        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise GroupingError("grouping requires unique element ids")
        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise GroupingError("grouping requires unique element order values")
        if orders != sorted(orders):
            raise GroupingError("grouping requires elements in ascending canonical source order")

        if signal_set.element_count != len(elements):
            raise GroupingError("structure signal element_count does not match grouping input")
        if boundary_set.element_count != len(elements):
            raise GroupingError("boundary element_count does not match grouping input")
        if boundary_set.signal_version != signal_set.version:
            raise GroupingError("boundary set was produced from a different signal version")
        if len(boundary_set.boundaries) != max(0, len(elements) - 1):
            raise GroupingError("boundary count does not match adjacent element pairs")

        for index, boundary in enumerate(boundary_set.boundaries):
            if boundary.left_element_id != elements[index].id or boundary.right_element_id != elements[index + 1].id:
                raise GroupingError(f"boundary {boundary.id!r} does not match canonical adjacency at index {index}")
