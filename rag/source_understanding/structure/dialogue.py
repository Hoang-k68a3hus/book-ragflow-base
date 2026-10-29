from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from rag.source_understanding.schemas.context import Identifier, SchemaModel
from rag.source_understanding.schemas.element import Element, ElementType

from .boundary import BoundaryClass, BoundarySet


class DialogueSegmentCandidate(SchemaModel):
    element_ids: tuple[Identifier, ...] = Field(min_length=2)
    boundary_ids: tuple[Identifier, ...] = Field(default_factory=tuple)


class DialogueSegmentBuilder:
    """Find maximal contiguous dialogue runs without topic inference."""

    def detect(
        self,
        elements: Sequence[Element],
        boundary_set: BoundarySet,
        *,
        min_turns: int = 2,
    ) -> tuple[DialogueSegmentCandidate, ...]:
        if min_turns < 2:
            raise ValueError("min_turns must be at least 2")

        snapshot = tuple(elements)
        candidates: list[DialogueSegmentCandidate] = []
        start: int | None = None

        def flush(end: int) -> None:
            nonlocal start
            if start is not None and end - start >= min_turns:
                candidates.append(
                    DialogueSegmentCandidate(
                        element_ids=tuple(element.id for element in snapshot[start:end]),
                        boundary_ids=tuple(boundary_set.boundaries[index].id for index in range(start, end - 1)),
                    )
                )
            start = None

        for index, element in enumerate(snapshot):
            if element.type != ElementType.DIALOGUE_TURN:
                flush(index)
                continue

            if start is None:
                start = index
                continue

            boundary = boundary_set.boundaries[index - 1]
            if boundary.classification in {BoundaryClass.HARD, BoundaryClass.UNKNOWN}:
                flush(index)
                start = index

        flush(len(snapshot))
        return tuple(candidates)
