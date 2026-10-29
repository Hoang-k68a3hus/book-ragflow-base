from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import (
    Confidence,
    ContextNode,
    Identifier,
    SchemaModel,
    StructureMode,
    StructureSource,
)
from rag.source_understanding.schemas.document import DocumentStructure
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.source_attributes import SourceAttributeError, source_label, source_zone

from .boundary import BoundaryClass, BoundaryDecision, BoundaryReason, BoundarySet
from .signals import StructureSignal, StructureSignalKind, StructureSignalSet

HIERARCHY_VERSION = "6"
HIERARCHY_POLICY_VERSION = "3"

_HIERARCHICAL_NUMBERING_RE = re.compile(r"^\d+(?:\.\d+)+$")
_FLAT_NUMBERING_RE = re.compile(r"^(?:\d+|[A-Za-z]|[IVXLCDMivxlcdm]+)$")
_NAVIGATION_ENTRY_RE = re.compile(r"^\s*\S.*(?:\t+|\.{2,}|(?:\s+\.\s*){2,})\s*\d+\s*$")
_GLOSSARY_ENTRY_RE = re.compile(r"^[A-Z][A-Z0-9&/.-]{1,15}\s+\S.{12,}(?::|\s[-–—]\s)")
_FRONT_MATTER_METADATA_RE = re.compile(
    r"^(?:nha xuat ban|publisher|copyright|isbn\b|ban quyen|tai ban|bien muc)",
)
_TOC_LABELS = frozenset({"muc luc", "contents", "table of contents"})


class HierarchyError(ValueError):
    """Hierarchy inputs cannot support a trustworthy context interpretation."""


class HierarchyPolicy(SchemaModel):
    """Conservative deterministic hierarchy policy recorded with the result."""

    version: str = HIERARCHY_POLICY_VERSION
    explicit_node_confidence: Confidence = 0.95
    inferred_node_confidence: Confidence = 0.75
    derived_node_confidence: Confidence = 0.80
    inferred_root_confidence: Confidence = 0.80
    normalized_level_confidence: Confidence = 0.80
    local_structure_confidence: Confidence = 0.60
    grouped_structure_confidence: Confidence = 0.70
    hierarchical_structure_confidence: Confidence = 0.85
    max_label_length: int = Field(default=2048, ge=1, le=2048)
    min_heading_count_for_leading_root: int = Field(default=3, ge=3, le=64)
    min_navigation_entries_for_leading_root: int = Field(default=3, ge=2, le=64)


class ElementContextAssignment(SchemaModel):
    element_id: Identifier
    context_node_ids: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_unique_contexts(self) -> ElementContextAssignment:
        if len(self.context_node_ids) != len(set(self.context_node_ids)):
            raise ValueError("context_node_ids must be unique")
        return self


class HierarchyResult(SchemaModel):
    version: str = HIERARCHY_VERSION
    element_count: int = Field(ge=1)
    signal_version: str
    boundary_version: str
    policy: HierarchyPolicy
    context_nodes: tuple[ContextNode, ...] = Field(default_factory=tuple)
    assignments: tuple[ElementContextAssignment, ...]
    structure: DocumentStructure
    suppressed_context_element_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    suppression_reasons: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> HierarchyResult:
        if len(self.assignments) != self.element_count:
            raise ValueError("hierarchy assignments must cover every input element")
        node_ids = [node.id for node in self.context_nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("hierarchy context node ids must be unique")
        valid_nodes = set(node_ids)
        assignment_ids = [assignment.element_id for assignment in self.assignments]
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError("hierarchy assignments must use unique element ids")
        for assignment in self.assignments:
            missing = set(assignment.context_node_ids) - valid_nodes
            if missing:
                raise ValueError(f"hierarchy assignment references unknown context nodes: {sorted(missing)}")
        by_id = {node.id: node for node in self.context_nodes}
        for node in self.context_nodes:
            if node.parent_id is None:
                continue
            parent = by_id.get(node.parent_id)
            if parent is None:
                raise ValueError(f"context node {node.id!r} references unknown parent {node.parent_id!r}")
            if node.level is not None and parent.level is not None and parent.level >= node.level:
                raise ValueError("context parent level must be shallower than child level")
        return self


@dataclass(frozen=True)
class _ContextCandidate:
    element: Element
    node_type: str
    label: str
    level: int
    source: StructureSource
    confidence: float
    signal_ids: tuple[str, ...]
    level_source: str
    label_truncated: bool
    native_heading_level: int | None
    has_numbering_marker: bool


class HierarchyBuilder:
    """Build conservative context nodes without mutating source-near elements."""

    version: str = HIERARCHY_VERSION

    def __init__(self, policy: HierarchyPolicy | None = None) -> None:
        self._policy = policy if policy is not None else HierarchyPolicy()

    def build(
        self,
        elements: Sequence[Element],
        signal_set: StructureSignalSet,
        boundary_set: BoundarySet,
    ) -> HierarchyResult:
        snapshot = tuple(elements)
        self._validate_inputs(snapshot, signal_set, boundary_set)
        signals_by_element = self._signals_by_element(snapshot, signal_set)
        boundary_before = {boundary.right_element_id: boundary for boundary in boundary_set.boundaries}

        candidates: dict[str, _ContextCandidate] = {}
        suppression_reasons: dict[str, str] = {}
        for element in snapshot:
            incoming_boundary = boundary_before.get(element.id)
            type_signal = next(signal for signal in signals_by_element[element.id] if signal.kind == StructureSignalKind.ELEMENT_TYPE)
            if (
                element.type in {ElementType.TITLE, ElementType.HEADING}
                and type_signal.source != StructureSource.EXPLICIT
                and incoming_boundary is not None
                and incoming_boundary.classification not in {BoundaryClass.HARD, BoundaryClass.SOFT}
            ):
                suppression_reasons[element.id] = "INFERRED_STRUCTURAL_WITHOUT_CONFIDENT_BOUNDARY"
                continue
            candidate = self._candidate(
                element,
                signals_by_element[element.id],
                incoming_boundary,
            )
            if candidate is not None:
                reason = self._context_suppression_reason(element, candidate.label)
                if reason is None:
                    candidates[element.id] = candidate
                else:
                    suppression_reasons[element.id] = reason

        candidates = self._calibrate_leading_document_root(snapshot, candidates)

        nodes: list[ContextNode] = []
        assignments: list[ElementContextAssignment] = []
        stack: list[ContextNode] = []
        for element in snapshot:
            candidate = candidates.get(element.id)
            if candidate is not None:
                while stack and stack[-1].level is not None and stack[-1].level >= candidate.level:
                    stack.pop()
                parent_id = stack[-1].id if stack else None
                node = self._make_node(candidate, parent_id)
                nodes.append(node)
                stack.append(node)
            assignments.append(
                ElementContextAssignment(
                    element_id=element.id,
                    context_node_ids=tuple(node.id for node in stack),
                )
            )

        structure = self._structure(tuple(nodes), len(snapshot), len(suppression_reasons))
        return HierarchyResult(
            element_count=len(snapshot),
            signal_version=signal_set.version,
            boundary_version=boundary_set.version,
            policy=self._policy,
            context_nodes=tuple(nodes),
            assignments=tuple(assignments),
            structure=structure,
            suppressed_context_element_ids=tuple(suppression_reasons),
            suppression_reasons=suppression_reasons,
        )

    @staticmethod
    def _context_suppression_reason(element: Element, label: str) -> str | None:
        zone = source_zone(element)
        if zone == "navigation":
            return "NAVIGATION_OR_TOC"
        if zone in {"header", "footer"}:
            return "BOILERPLATE"
        normalized = " ".join("".join(char for char in unicodedata.normalize("NFKD", label) if not unicodedata.combining(char)).casefold().split())
        if normalized in _TOC_LABELS or _NAVIGATION_ENTRY_RE.fullmatch(label):
            return "NAVIGATION_OR_TOC"
        page = element.location.page if element.location is not None else None
        if (page is None or page <= 5) and _FRONT_MATTER_METADATA_RE.match(normalized):
            return "FRONT_MATTER_METADATA"
        if _GLOSSARY_ENTRY_RE.match(label.strip()):
            return "GLOSSARY_DEFINITION"
        return None

    def _candidate(
        self,
        element: Element,
        signals: tuple[StructureSignal, ...],
        incoming_boundary: BoundaryDecision | None,
    ) -> _ContextCandidate | None:
        outline_signal = next(
            (signal for signal in signals if signal.kind == StructureSignalKind.OUTLINE_LEVEL),
            None,
        )
        source_structural = element.type in {ElementType.TITLE, ElementType.HEADING}
        if not source_structural and outline_signal is None:
            return None

        type_signal = next(signal for signal in signals if signal.kind == StructureSignalKind.ELEMENT_TYPE)
        if source_structural:
            self._validate_structural_boundary(element, type_signal, incoming_boundary)
        elif incoming_boundary is not None and incoming_boundary.classification == BoundaryClass.UNKNOWN:
            raise HierarchyError(f"inferred outline element {element.id!r} cannot cross an UNKNOWN boundary")

        try:
            explicit_source_label = source_label(element)
        except SourceAttributeError as exc:
            raise HierarchyError(str(exc)) from exc
        text = explicit_source_label if explicit_source_label is not None else element.text
        if text is None or not text.strip():
            return None
        stripped = text.strip()
        label_truncated = len(stripped) > self._policy.max_label_length
        label = stripped[: self._policy.max_label_length]

        heading_level_signal = next(
            (signal for signal in signals if signal.kind == StructureSignalKind.HEADING_LEVEL),
            None,
        )
        numbering_signal = next(
            (signal for signal in signals if signal.kind == StructureSignalKind.NUMBERING_MARKER),
            None,
        )
        numbering_level_signal = next(
            (signal for signal in signals if signal.kind == StructureSignalKind.NUMBERING_LEVEL),
            None,
        )

        native_heading_level: int | None = None
        node_type = element.type.value
        node_source = type_signal.source
        if outline_signal is not None:
            numeric = outline_signal.numeric_value
            if numeric is None or int(numeric) != numeric or not (0 <= numeric <= 64):
                raise HierarchyError(f"OUTLINE_LEVEL signal for {element.id!r} must be an integer in [0, 64]")
            role = outline_signal.metadata.get("context_role")
            if not isinstance(role, str) or not role.strip() or len(role) > 128:
                raise HierarchyError(f"OUTLINE_LEVEL signal for {element.id!r} requires a context_role")
            level = int(numeric)
            node_type = role
            node_source = outline_signal.source
            level_source = "OUTLINE_LEVEL"
        elif element.type == ElementType.TITLE:
            level = 0
            level_source = "ELEMENT_TYPE"
        elif heading_level_signal is not None:
            numeric = heading_level_signal.numeric_value
            if numeric is None or int(numeric) != numeric or not (1 <= numeric <= 64):
                raise HierarchyError(f"HEADING_LEVEL signal for {element.id!r} must be an integer in [1, 64]")
            native_heading_level = int(numeric)
            level = native_heading_level
            level_source = "HEADING_LEVEL"
        elif numbering_signal is not None:
            parsed = self._numbering_level(numbering_signal.text_value)
            level = 1 if parsed is None else parsed
            level_source = "NUMBERING_MARKER"
        else:
            level = 1
            level_source = "DEFAULT_HEADING_LEVEL"

        if heading_level_signal is not None:
            numeric = heading_level_signal.numeric_value
            if numeric is not None and int(numeric) == numeric and 1 <= numeric <= 64:
                native_heading_level = int(numeric)

        base_confidence = {
            StructureSource.EXPLICIT: self._policy.explicit_node_confidence,
            StructureSource.INFERRED: self._policy.inferred_node_confidence,
            StructureSource.DERIVED: self._policy.derived_node_confidence,
        }[node_source]
        confidence_values = [base_confidence]
        for signal in (
            type_signal,
            heading_level_signal,
            numbering_signal,
            numbering_level_signal,
            outline_signal,
        ):
            if signal is not None and signal.confidence is not None:
                confidence_values.append(signal.confidence)

        supporting_signal_ids = [type_signal.id]
        for signal in (
            heading_level_signal,
            numbering_signal,
            numbering_level_signal,
            outline_signal,
        ):
            if signal is not None and signal.id not in supporting_signal_ids:
                supporting_signal_ids.append(signal.id)
        supporting_signal_ids.extend(
            signal.id
            for signal in signals
            if signal.kind
            in {
                StructureSignalKind.NUMBERING_FORMAT,
                StructureSignalKind.SECTION_MARKER,
                StructureSignalKind.STYLE_BOLD,
                StructureSignalKind.STYLE_FONT_SIZE,
                StructureSignalKind.STYLE_INDENTATION,
            }
            and signal.id not in supporting_signal_ids
        )

        return _ContextCandidate(
            element=element,
            node_type=node_type,
            label=label,
            level=level,
            source=node_source,
            confidence=min(confidence_values),
            signal_ids=tuple(supporting_signal_ids),
            level_source=level_source,
            label_truncated=label_truncated,
            native_heading_level=native_heading_level,
            has_numbering_marker=(numbering_signal is not None or numbering_level_signal is not None),
        )

    @staticmethod
    def _validate_structural_boundary(
        element: Element,
        type_signal: StructureSignal,
        incoming_boundary: BoundaryDecision | None,
    ) -> None:
        if incoming_boundary is None:
            return
        if type_signal.source == StructureSource.EXPLICIT:
            if incoming_boundary.classification != BoundaryClass.HARD or BoundaryReason.EXPLICIT_STRUCTURE_START not in incoming_boundary.reasons:
                raise HierarchyError(f"explicit structural element {element.id!r} must start at a HARD explicit-structure boundary")
            return
        if incoming_boundary.classification not in {
            BoundaryClass.HARD,
            BoundaryClass.SOFT,
        }:
            raise HierarchyError(f"inferred structural element {element.id!r} requires at least a SOFT incoming boundary")

    def _calibrate_leading_document_root(
        self,
        elements: tuple[Element, ...],
        candidates: dict[str, _ContextCandidate],
    ) -> dict[str, _ContextCandidate]:
        """Infer a root only from strong document-local layout patterns."""

        if any(candidate.level == 0 for candidate in candidates.values()):
            return candidates

        heading_candidates = [candidate for candidate in candidates.values() if candidate.element.type == ElementType.HEADING]
        heading_candidates.sort(key=lambda item: item.element.order)
        if len(heading_candidates) < self._policy.min_heading_count_for_leading_root:
            return candidates

        first = heading_candidates[0]
        if first.element.order != 0 or first.has_numbering_marker:
            return candidates

        second = heading_candidates[1]
        adjacent_pair = (
            second.element.order == first.element.order + 1 and first.native_heading_level is not None and first.native_heading_level == second.native_heading_level and len(heading_candidates) >= 4
        )
        navigation_block = (
            self._navigation_entry_count(
                elements,
                first.element.order + 1,
                second.element.order,
            )
            >= self._policy.min_navigation_entries_for_leading_root
        )

        if not adjacent_pair and not navigation_block:
            return candidates

        calibrated = dict(candidates)
        calibrated[first.element.id] = replace(
            first,
            node_type="DOCUMENT_TITLE",
            level=0,
            source=StructureSource.INFERRED,
            confidence=min(first.confidence, self._policy.inferred_root_confidence),
            level_source=("INFERRED_ADJACENT_TITLE_SUBTITLE_ROOT" if adjacent_pair else "INFERRED_NAVIGATION_PRECEDED_ROOT"),
        )

        section_start_index = 1
        if adjacent_pair:
            calibrated[second.element.id] = replace(
                second,
                node_type="DOCUMENT_SUBTITLE",
                level=1,
                source=StructureSource.INFERRED,
                confidence=min(
                    second.confidence,
                    self._policy.normalized_level_confidence,
                ),
                level_source="INFERRED_ADJACENT_DOCUMENT_SUBTITLE",
            )
            section_start_index = 2

        section_candidates = heading_candidates[section_start_index:]
        if not section_candidates:
            return calibrated
        baseline = next(
            (candidate.native_heading_level for candidate in section_candidates if candidate.native_heading_level is not None),
            None,
        )
        if baseline is None:
            return calibrated

        for candidate in section_candidates:
            native = candidate.native_heading_level
            if native is None or candidate.has_numbering_marker:
                continue
            normalized_level = 1 if native <= baseline else 1 + (native - baseline)
            if normalized_level == candidate.level:
                continue
            calibrated[candidate.element.id] = replace(
                candidate,
                level=normalized_level,
                source=StructureSource.INFERRED,
                confidence=min(
                    candidate.confidence,
                    self._policy.normalized_level_confidence,
                ),
                level_source="INFERRED_ROOT_RELATIVE_HEADING_LEVEL",
            )
        return calibrated

    @staticmethod
    def _navigation_entry_count(
        elements: tuple[Element, ...],
        start_order: int,
        end_order: int,
    ) -> int:
        count = 0
        for element in elements:
            if not (start_order <= element.order < end_order):
                continue
            text = element.text
            if text is not None and _NAVIGATION_ENTRY_RE.match(text):
                count += 1
        return count

    @staticmethod
    def _numbering_level(marker: str | None) -> int | None:
        if marker is None:
            return None
        normalized = marker.strip()
        if normalized.endswith((")", ".")):
            normalized = normalized[:-1]
        if _HIERARCHICAL_NUMBERING_RE.fullmatch(normalized):
            return normalized.count(".") + 1
        if _FLAT_NUMBERING_RE.fullmatch(normalized):
            return 1
        return None

    @staticmethod
    def _make_node(candidate: _ContextCandidate, parent_id: str | None) -> ContextNode:
        identity = f"{candidate.element.id}|{candidate.node_type}|{candidate.level}|{candidate.label}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return ContextNode(
            id=f"ctx_{digest}",
            type=candidate.node_type,
            label=candidate.label,
            level=candidate.level,
            source=candidate.source,
            confidence=candidate.confidence,
            parent_id=parent_id,
            attributes={
                "anchor_element_id": candidate.element.id,
                "signal_ids": list(candidate.signal_ids),
                "level_source": candidate.level_source,
                "parent_source": StructureSource.DERIVED.value,
                "label_truncated": candidate.label_truncated,
                "source_element_type": candidate.element.type.value,
                "source_heading_level": candidate.native_heading_level,
            },
        )

    def _structure(
        self,
        nodes: tuple[ContextNode, ...],
        element_count: int,
        suppressed_count: int,
    ) -> DocumentStructure:
        if not nodes:
            return DocumentStructure()
        explicit_ratio = sum(node.source == StructureSource.EXPLICIT for node in nodes) / len(nodes)
        nested_ratio = sum(node.parent_id is not None for node in nodes) / len(nodes)
        context_ratio = len(nodes) / element_count
        average_confidence = sum(node.confidence for node in nodes) / len(nodes)
        if len(nodes) == 1:
            mode = StructureMode.LOCAL
            confidence = min(self._policy.local_structure_confidence, average_confidence)
        elif any(node.parent_id is not None for node in nodes):
            mode = StructureMode.HIERARCHICAL
            confidence = min(
                self._policy.hierarchical_structure_confidence,
                average_confidence,
            )
        else:
            mode = StructureMode.GROUPED
            confidence = min(
                self._policy.grouped_structure_confidence,
                average_confidence,
            )
        return DocumentStructure(
            mode=mode,
            source=StructureSource.DERIVED,
            confidence=confidence,
            signals={
                "context_node_ratio": context_ratio,
                "nested_context_ratio": nested_ratio,
                "explicit_context_ratio": explicit_ratio,
                "suppressed_context_ratio": suppressed_count / element_count,
            },
        )

    @staticmethod
    def _signals_by_element(
        elements: tuple[Element, ...],
        signal_set: StructureSignalSet,
    ) -> dict[str, tuple[StructureSignal, ...]]:
        by_element: dict[str, list[StructureSignal]] = {element.id: [] for element in elements}
        for signal in signal_set.signals:
            for element_id in signal.element_ids:
                by_element[element_id].append(signal)
        return {element_id: tuple(signals) for element_id, signals in by_element.items()}

    @staticmethod
    def _validate_inputs(
        elements: tuple[Element, ...],
        signal_set: StructureSignalSet,
        boundary_set: BoundarySet,
    ) -> None:
        if not elements:
            raise HierarchyError("cannot build hierarchy from empty elements")
        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise HierarchyError("hierarchy requires unique element ids")
        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise HierarchyError("hierarchy requires unique element order values")
        if orders != sorted(orders):
            raise HierarchyError("hierarchy requires elements in ascending canonical source order")
        if signal_set.element_count != len(elements):
            raise HierarchyError("structure signal element_count does not match hierarchy input elements")
        if boundary_set.element_count != len(elements):
            raise HierarchyError("boundary element_count does not match hierarchy input elements")
        if boundary_set.signal_version != signal_set.version:
            raise HierarchyError("boundary signal_version does not match supplied structure signals")

        positions = {element.id: index for index, element in enumerate(elements)}
        signal_ids = [signal.id for signal in signal_set.signals]
        if len(signal_ids) != len(set(signal_ids)):
            raise HierarchyError("structure signal ids must be unique")

        type_signals: dict[str, list[StructureSignal]] = {element.id: [] for element in elements}
        heading_levels: dict[str, list[StructureSignal]] = {element.id: [] for element in elements}
        outline_levels: dict[str, list[StructureSignal]] = {element.id: [] for element in elements}
        for signal in signal_set.signals:
            unknown = set(signal.element_ids) - positions.keys()
            if unknown:
                raise HierarchyError(f"structure signal {signal.id!r} references unknown elements: {sorted(unknown)}")
            if signal.kind == StructureSignalKind.ELEMENT_TYPE:
                if len(signal.element_ids) != 1:
                    raise HierarchyError("ELEMENT_TYPE signals must reference exactly one element")
                type_signals[signal.element_ids[0]].append(signal)
            if signal.kind == StructureSignalKind.HEADING_LEVEL:
                if len(signal.element_ids) != 1:
                    raise HierarchyError("HEADING_LEVEL signals must reference exactly one element")
                heading_levels[signal.element_ids[0]].append(signal)
            if signal.kind == StructureSignalKind.OUTLINE_LEVEL:
                if len(signal.element_ids) != 1:
                    raise HierarchyError("OUTLINE_LEVEL signals must reference exactly one element")
                outline_levels[signal.element_ids[0]].append(signal)

        for element in elements:
            matches = type_signals[element.id]
            if len(matches) != 1:
                raise HierarchyError(f"element {element.id!r} must have exactly one ELEMENT_TYPE signal")
            signal = matches[0]
            if signal.text_value != element.type.value:
                raise HierarchyError(f"ELEMENT_TYPE signal disagrees with canonical type for {element.id!r}")
            if signal.source != element.provenance.source:
                raise HierarchyError(f"ELEMENT_TYPE signal provenance disagrees for {element.id!r}")
            if signal.confidence != element.confidence.type:
                raise HierarchyError(f"ELEMENT_TYPE signal confidence disagrees for {element.id!r}")
            levels = heading_levels[element.id]
            if len(levels) > 1:
                raise HierarchyError(f"element {element.id!r} has multiple HEADING_LEVEL signals")
            if levels and element.type != ElementType.HEADING:
                raise HierarchyError("HEADING_LEVEL signal may only target HEADING elements")
            if len(outline_levels[element.id]) > 1:
                raise HierarchyError(f"element {element.id!r} has multiple OUTLINE_LEVEL signals")

        if len(boundary_set.boundaries) != max(0, len(elements) - 1):
            raise HierarchyError("boundary count does not match adjacent element pairs")
        for index, boundary in enumerate(boundary_set.boundaries):
            left = elements[index]
            right = elements[index + 1]
            if boundary.left_element_id != left.id or boundary.right_element_id != right.id:
                raise HierarchyError("boundary decisions must follow canonical adjacent element order")
