from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import Identifier, SchemaModel, StructureSource
from rag.source_understanding.schemas.element import Element, ElementType

from .boundary import BoundaryClass, BoundaryIntegrityGuard, BoundarySet
from .signals import StructureSignal, StructureSignalKind, StructureSignalSet


class QAPairRule(StrEnum):
    EXPLICIT_TYPE = "EXPLICIT_TYPE"
    LEXICAL_MARKER = "LEXICAL_MARKER"


class QAPairCandidate(SchemaModel):
    question_element_id: Identifier
    answer_element_id: Identifier
    source: StructureSource
    rule: QAPairRule
    boundary_id: Identifier
    signal_ids: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_pair(self) -> QAPairCandidate:
        if self.question_element_id == self.answer_element_id:
            raise ValueError("Q/A pair requires distinct elements")
        if len(self.signal_ids) != len(set(self.signal_ids)):
            raise ValueError("Q/A candidate signal_ids must be unique")
        return self


class QAPairBuilder:
    """Detect only adjacent Q/A pairs already protected by the boundary layer."""

    def detect(
        self,
        elements: Sequence[Element],
        signal_set: StructureSignalSet,
        boundary_set: BoundarySet,
    ) -> tuple[QAPairCandidate, ...]:
        snapshot = tuple(elements)
        by_element = self._signals_by_element(snapshot, signal_set)
        candidates: list[QAPairCandidate] = []
        index = 0
        while index < len(snapshot) - 1:
            left = snapshot[index]
            right = snapshot[index + 1]
            boundary = boundary_set.boundaries[index]

            if boundary.classification == BoundaryClass.NONE and boundary.integrity_guard == BoundaryIntegrityGuard.QA_PAIR:
                explicit = left.type == ElementType.QUESTION and right.type == ElementType.ANSWER
                left_kinds = {signal.kind for signal in by_element[left.id]}
                right_kinds = {signal.kind for signal in by_element[right.id]}
                lexical = StructureSignalKind.QUESTION_MARKER in left_kinds and StructureSignalKind.ANSWER_MARKER in right_kinds

                if explicit or lexical:
                    rule = QAPairRule.EXPLICIT_TYPE if explicit else QAPairRule.LEXICAL_MARKER
                    source = StructureSource.DERIVED if explicit else StructureSource.INFERRED
                    relevant = tuple(
                        signal.id
                        for signal in (*by_element[left.id], *by_element[right.id])
                        if signal.kind
                        in {
                            StructureSignalKind.QUESTION_MARKER,
                            StructureSignalKind.ANSWER_MARKER,
                            StructureSignalKind.ELEMENT_TYPE,
                        }
                    )
                    candidates.append(
                        QAPairCandidate(
                            question_element_id=left.id,
                            answer_element_id=right.id,
                            source=source,
                            rule=rule,
                            boundary_id=boundary.id,
                            signal_ids=tuple(dict.fromkeys(relevant)),
                        )
                    )
                    index += 2
                    continue
            index += 1

        return tuple(candidates)

    @staticmethod
    def _signals_by_element(
        elements: tuple[Element, ...],
        signal_set: StructureSignalSet,
    ) -> dict[str, tuple[StructureSignal, ...]]:
        result: dict[str, list[StructureSignal]] = {element.id: [] for element in elements}
        for signal in signal_set.signals:
            for element_id in signal.element_ids:
                if element_id in result:
                    result[element_id].append(signal)
        return {key: tuple(value) for key, value in result.items()}
