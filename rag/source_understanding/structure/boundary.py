from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import (
    Confidence,
    FiniteFloat,
    Identifier,
    SchemaModel,
    StructureSource,
)
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.source_attributes import SourceAttributeError, source_integrity_group_id

from .signals import StructureSignal, StructureSignalKind, StructureSignalSet

BOUNDARY_VERSION = "2"
BOUNDARY_POLICY_VERSION = "1"


class BoundaryError(ValueError):
    """Boundary evidence cannot be evaluated without trustworthy local inputs."""


class BoundaryClass(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class BoundaryReason(StrEnum):
    EXPLICIT_STRUCTURE_START = "EXPLICIT_STRUCTURE_START"
    SEPARATOR = "SEPARATOR"
    TABLE_BOUNDARY = "TABLE_BOUNDARY"
    CODE_BOUNDARY = "CODE_BOUNDARY"
    NATIVE_INTEGRITY_BOUNDARY = "NATIVE_INTEGRITY_BOUNDARY"
    NATIVE_INTEGRITY_CONTINUITY = "NATIVE_INTEGRITY_CONTINUITY"
    CONTENT_TYPE_CHANGE = "CONTENT_TYPE_CHANGE"
    STYLE_CHANGE = "STYLE_CHANGE"
    PATTERN_START = "PATTERN_START"
    PARAGRAPH_BREAK = "PARAGRAPH_BREAK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONTENT_INTEGRITY_UNRESOLVED = "CONTENT_INTEGRITY_UNRESOLVED"


class BoundaryIntegrityGuard(StrEnum):
    QA_PAIR = "QA_PAIR"
    NATIVE_INTEGRITY_GROUP = "NATIVE_INTEGRITY_GROUP"


class BoundaryPolicy(SchemaModel):
    """Configurable baseline policy; weights are evaluation parameters, not source facts."""

    version: str = BOUNDARY_POLICY_VERSION
    explicit_weight: FiniteFloat = 1.0
    style_weight: FiniteFloat = 0.25
    type_change_weight: FiniteFloat = 0.30
    separator_weight: FiniteFloat = 1.0
    pattern_weight: FiniteFloat = 0.45
    paragraph_break_weight: FiniteFloat = 0.30
    soft_threshold: FiniteFloat = 0.25
    hard_threshold: FiniteFloat = 0.80

    @model_validator(mode="after")
    def validate_policy(self) -> BoundaryPolicy:
        for field_name in (
            "explicit_weight",
            "style_weight",
            "type_change_weight",
            "separator_weight",
            "pattern_weight",
            "paragraph_break_weight",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.soft_threshold < 0 or self.hard_threshold < 0:
            raise ValueError("boundary thresholds must be non-negative")
        if self.soft_threshold >= self.hard_threshold:
            raise ValueError("soft_threshold must be lower than hard_threshold")
        return self


class BoundaryComponents(SchemaModel):
    explicit: Confidence = 0.0
    style_change: Confidence = 0.0
    type_change: Confidence = 0.0
    separator: Confidence = 0.0
    pattern_start: Confidence = 0.0
    paragraph_break: Confidence = 0.0


class BoundaryDecision(SchemaModel):
    """A derived decision for one adjacent canonical element pair."""

    id: Identifier
    left_element_id: Identifier
    right_element_id: Identifier
    classification: BoundaryClass
    score: FiniteFloat
    source: StructureSource = StructureSource.DERIVED
    reasons: tuple[BoundaryReason, ...] = Field(default_factory=tuple)
    integrity_guard: BoundaryIntegrityGuard | None = None
    components: BoundaryComponents = Field(default_factory=BoundaryComponents)
    signal_ids: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_decision(self) -> BoundaryDecision:
        if self.left_element_id == self.right_element_id:
            raise ValueError("boundary decision requires two distinct elements")
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("boundary reasons must be unique")
        if len(self.signal_ids) != len(set(self.signal_ids)):
            raise ValueError("boundary signal_ids must be unique")
        if self.integrity_guard is not None and self.classification != BoundaryClass.NONE:
            raise ValueError("integrity-guarded boundaries must classify as NONE")
        if self.source != StructureSource.DERIVED:
            raise ValueError("boundary decisions are derived from structural evidence")
        return self


class BoundarySet(SchemaModel):
    version: str = BOUNDARY_VERSION
    element_count: int = Field(ge=1)
    signal_version: str
    policy: BoundaryPolicy
    boundaries: tuple[BoundaryDecision, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_boundary_count(self) -> BoundarySet:
        expected = max(0, self.element_count - 1)
        if len(self.boundaries) != expected:
            raise ValueError(f"boundary count must equal adjacent pair count {expected}, got {len(self.boundaries)}")
        return self


_TABLE_TYPES = frozenset({ElementType.TABLE, ElementType.TABLE_ROW, ElementType.TABLE_CELL})
_LIST_TYPES = frozenset({ElementType.LIST, ElementType.LIST_ITEM})
_CODE_TYPES = frozenset({ElementType.CODE})
_PATTERN_START_KINDS = frozenset(
    {
        StructureSignalKind.NUMBERING_MARKER,
        StructureSignalKind.SECTION_MARKER,
        StructureSignalKind.QUESTION_MARKER,
        StructureSignalKind.TIMESTAMP_PATTERN,
        StructureSignalKind.SPEAKER_LABEL_CANDIDATE,
    }
)


class BoundaryScorer:
    """Aggregate local signals into boundary classes without building groups/hierarchy."""

    version: str = BOUNDARY_VERSION

    def __init__(self, policy: BoundaryPolicy | None = None) -> None:
        self._policy = policy if policy is not None else BoundaryPolicy()

    def score(
        self,
        elements: Sequence[Element],
        signal_set: StructureSignalSet,
    ) -> BoundarySet:
        snapshot = tuple(elements)
        self._validate_inputs(snapshot, signal_set)
        signal_positions = self._index_signals(snapshot, signal_set)
        boundaries = tuple(self._score_pair(snapshot[index], snapshot[index + 1], signal_positions) for index in range(len(snapshot) - 1))
        return BoundarySet(
            element_count=len(snapshot),
            signal_version=signal_set.version,
            policy=self._policy,
            boundaries=boundaries,
        )

    def _score_pair(
        self,
        left: Element,
        right: Element,
        signal_positions: dict[str, tuple[StructureSignal, ...]],
    ) -> BoundaryDecision:
        local_signals = self._local_signals(left.id, right.id, signal_positions)
        left_signals = tuple(signal for signal in local_signals if signal.element_ids == (left.id,))
        right_signals = tuple(signal for signal in local_signals if signal.element_ids == (right.id,))

        native_relation = self._native_integrity_relation(left, right)
        integrity_guard = self._integrity_guard(left, right, left_signals, right_signals, native_relation)
        explicit_start = self._has_explicit_structure_start(right, right_signals)
        separator = left.type == ElementType.SEPARATOR or right.type == ElementType.SEPARATOR
        shared_integrity_family = self._shared_integrity_family(left, right)
        type_change = left.type != right.type and not shared_integrity_family
        style_change = 0.0 if shared_integrity_family else self._style_change(left, right)
        pattern_start = any(signal.kind in _PATTERN_START_KINDS for signal in right_signals)
        paragraph_break = left.type == ElementType.PARAGRAPH and right.type == ElementType.PARAGRAPH

        components = BoundaryComponents(
            explicit=float(explicit_start),
            style_change=style_change,
            type_change=float(type_change),
            separator=float(separator),
            pattern_start=float(pattern_start),
            paragraph_break=float(paragraph_break),
        )
        score = (
            self._policy.explicit_weight * components.explicit
            + self._policy.style_weight * components.style_change
            + self._policy.type_change_weight * components.type_change
            + self._policy.separator_weight * components.separator
            + self._policy.pattern_weight * components.pattern_start
            + self._policy.paragraph_break_weight * components.paragraph_break
        )

        reasons: list[BoundaryReason] = []
        if explicit_start:
            reasons.append(BoundaryReason.EXPLICIT_STRUCTURE_START)
        if separator:
            reasons.append(BoundaryReason.SEPARATOR)
        if type_change:
            reasons.append(BoundaryReason.CONTENT_TYPE_CHANGE)
        if style_change > 0:
            reasons.append(BoundaryReason.STYLE_CHANGE)
        if pattern_start:
            reasons.append(BoundaryReason.PATTERN_START)
        if paragraph_break:
            reasons.append(BoundaryReason.PARAGRAPH_BREAK)
        if native_relation == "SAME":
            reasons.append(BoundaryReason.NATIVE_INTEGRITY_CONTINUITY)
        elif native_relation == "DIFFERENT":
            reasons.append(BoundaryReason.NATIVE_INTEGRITY_BOUNDARY)

        hard_reason = self._hard_integrity_boundary(left, right)
        if hard_reason is not None and hard_reason not in reasons:
            reasons.append(hard_reason)

        if integrity_guard is not None:
            classification = BoundaryClass.NONE
        elif native_relation == "DIFFERENT" or explicit_start or separator or hard_reason is not None:
            classification = BoundaryClass.HARD
        elif self._is_unknown_pair(left, right, score):
            classification = BoundaryClass.UNKNOWN
            reasons.append(BoundaryReason.INSUFFICIENT_EVIDENCE)
            if shared_integrity_family:
                reasons.append(BoundaryReason.CONTENT_INTEGRITY_UNRESOLVED)
        elif score >= self._policy.hard_threshold:
            classification = BoundaryClass.HARD
        elif score >= self._policy.soft_threshold:
            classification = BoundaryClass.SOFT
        else:
            classification = BoundaryClass.NONE

        identity = f"{left.id}|{right.id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return BoundaryDecision(
            id=f"bnd_{digest}",
            left_element_id=left.id,
            right_element_id=right.id,
            classification=classification,
            score=score,
            reasons=tuple(reasons),
            integrity_guard=integrity_guard,
            components=components,
            signal_ids=tuple(signal.id for signal in local_signals),
        )

    @staticmethod
    def _native_integrity_relation(left: Element, right: Element) -> str | None:
        try:
            left_group = source_integrity_group_id(left)
            right_group = source_integrity_group_id(right)
        except SourceAttributeError as exc:
            raise BoundaryError(str(exc)) from exc
        if left_group is None or right_group is None:
            return None
        return "SAME" if left_group == right_group else "DIFFERENT"

    @staticmethod
    def _integrity_guard(
        left: Element,
        right: Element,
        left_signals: tuple[StructureSignal, ...],
        right_signals: tuple[StructureSignal, ...],
        native_relation: str | None,
    ) -> BoundaryIntegrityGuard | None:
        if native_relation == "SAME":
            return BoundaryIntegrityGuard.NATIVE_INTEGRITY_GROUP
        if left.type == ElementType.QUESTION and right.type == ElementType.ANSWER:
            return BoundaryIntegrityGuard.QA_PAIR
        left_kinds = {signal.kind for signal in left_signals}
        right_kinds = {signal.kind for signal in right_signals}
        if StructureSignalKind.QUESTION_MARKER in left_kinds and StructureSignalKind.ANSWER_MARKER in right_kinds:
            return BoundaryIntegrityGuard.QA_PAIR
        return None

    @staticmethod
    def _hard_integrity_boundary(left: Element, right: Element) -> BoundaryReason | None:
        if (left.type in _TABLE_TYPES) != (right.type in _TABLE_TYPES):
            return BoundaryReason.TABLE_BOUNDARY
        if (left.type in _CODE_TYPES) != (right.type in _CODE_TYPES):
            return BoundaryReason.CODE_BOUNDARY
        return None

    @staticmethod
    def _has_explicit_structure_start(
        right: Element,
        right_signals: tuple[StructureSignal, ...],
    ) -> bool:
        if right.type not in {ElementType.TITLE, ElementType.HEADING}:
            return False
        return any(signal.kind == StructureSignalKind.ELEMENT_TYPE and signal.source == StructureSource.EXPLICIT and signal.text_value == right.type.value for signal in right_signals)

    @staticmethod
    def _style_change(left: Element, right: Element) -> float:
        if left.style is None or right.style is None:
            return 0.0
        comparable = 0
        changed = 0
        for field_name in ("bold", "font_size", "indentation", "alignment"):
            left_value = getattr(left.style, field_name)
            right_value = getattr(right.style, field_name)
            if left_value is None or right_value is None:
                continue
            comparable += 1
            changed += left_value != right_value
        if comparable == 0:
            return 0.0
        return changed / comparable

    @staticmethod
    def _shared_integrity_family(left: Element, right: Element) -> bool:
        return any(left.type in family and right.type in family for family in (_TABLE_TYPES, _CODE_TYPES, _LIST_TYPES))

    @classmethod
    def _is_unknown_pair(cls, left: Element, right: Element, score: float) -> bool:
        return score == 0.0 and ((left.type == ElementType.UNKNOWN and right.type == ElementType.UNKNOWN) or cls._shared_integrity_family(left, right))

    @staticmethod
    def _local_signals(
        left_id: str,
        right_id: str,
        signal_positions: dict[str, tuple[StructureSignal, ...]],
    ) -> tuple[StructureSignal, ...]:
        allowed = {left_id, right_id}
        seen: set[str] = set()
        local: list[StructureSignal] = []
        for signal in (*signal_positions[left_id], *signal_positions[right_id]):
            if signal.id in seen:
                continue
            if not set(signal.element_ids).issubset(allowed):
                continue
            if len(signal.element_ids) == 2 and signal.element_ids != (left_id, right_id):
                continue
            seen.add(signal.id)
            local.append(signal)
        return tuple(local)

    @staticmethod
    def _index_signals(
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
    ) -> None:
        if not elements:
            raise BoundaryError("cannot score boundaries for an empty element sequence")
        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise BoundaryError("boundary scorer requires unique element ids")
        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise BoundaryError("boundary scorer requires unique element order values")
        if orders != sorted(orders):
            raise BoundaryError("boundary scorer requires elements in ascending canonical source order")
        if signal_set.element_count != len(elements):
            raise BoundaryError("structure signal element_count does not match boundary input elements")
        signal_ids = [signal.id for signal in signal_set.signals]
        if len(signal_ids) != len(set(signal_ids)):
            raise BoundaryError("structure signal ids must be unique")
        positions = {element.id: index for index, element in enumerate(elements)}
        type_signals: dict[str, list[StructureSignal]] = {element.id: [] for element in elements}
        for signal in signal_set.signals:
            unknown = set(signal.element_ids) - positions.keys()
            if unknown:
                raise BoundaryError(f"structure signal {signal.id!r} references unknown elements: {sorted(unknown)}")
            if signal.kind == StructureSignalKind.ELEMENT_TYPE:
                if len(signal.element_ids) != 1:
                    raise BoundaryError("ELEMENT_TYPE signals must reference exactly one element")
                type_signals[signal.element_ids[0]].append(signal)
            if signal.kind == StructureSignalKind.ELEMENT_TYPE_TRANSITION:
                if len(signal.element_ids) != 2:
                    raise BoundaryError("ELEMENT_TYPE_TRANSITION signals must reference exactly two elements")
                left_id, right_id = signal.element_ids
                if positions[right_id] != positions[left_id] + 1:
                    raise BoundaryError("ELEMENT_TYPE_TRANSITION signals must follow canonical adjacency")
        for element in elements:
            matches = type_signals[element.id]
            if len(matches) != 1:
                raise BoundaryError(f"element {element.id!r} must have exactly one ELEMENT_TYPE signal")
            signal = matches[0]
            if signal.text_value != element.type.value:
                raise BoundaryError(f"ELEMENT_TYPE signal disagrees with canonical type for {element.id!r}")
            if signal.source != element.provenance.source:
                raise BoundaryError(f"ELEMENT_TYPE signal provenance disagrees for {element.id!r}")
            if signal.confidence != element.confidence.type:
                raise BoundaryError(f"ELEMENT_TYPE signal confidence disagrees for {element.id!r}")
