from __future__ import annotations

import hashlib
from collections.abc import Sequence

from rag.source_understanding.schemas.context import Confidence, StructureSource
from rag.source_understanding.schemas.document import SubDocument
from rag.source_understanding.schemas.element import Element, ElementType

from .boundary import BoundaryClass, BoundaryReason, BoundarySet
from .signals import StructureSignalKind, StructureSignalSet


class SubDocumentDetector:
    """Infer pasted-document spans only from repeated explicit TITLE starts."""

    def detect(
        self,
        elements: Sequence[Element],
        signal_set: StructureSignalSet,
        boundary_set: BoundarySet,
        *,
        confidence: Confidence,
    ) -> tuple[SubDocument, ...]:
        snapshot = tuple(elements)
        explicit_title_ids = {
            signal.element_ids[0]
            for signal in signal_set.signals
            if signal.kind == StructureSignalKind.ELEMENT_TYPE and len(signal.element_ids) == 1 and signal.source == StructureSource.EXPLICIT and signal.text_value == ElementType.TITLE.value
        }

        starts: list[int] = []
        for index, element in enumerate(snapshot):
            if element.type != ElementType.TITLE or element.id not in explicit_title_ids:
                continue
            if index == 0:
                starts.append(index)
                continue
            boundary = boundary_set.boundaries[index - 1]
            if boundary.classification == BoundaryClass.HARD and BoundaryReason.EXPLICIT_STRUCTURE_START in boundary.reasons:
                starts.append(index)

        if len(starts) < 2:
            return ()

        result: list[SubDocument] = []
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(snapshot)
            if position + 1 < len(starts):
                separator_index = end - 1
                if separator_index >= start and snapshot[separator_index].type == ElementType.SEPARATOR:
                    end = separator_index
            if end <= start:
                continue

            member_ids = tuple(element.id for element in snapshot[start:end])
            title = snapshot[start]
            identity = "|".join(member_ids)
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            label = title.text
            if label is not None and len(label) > 2048:
                label = None
            result.append(
                SubDocument(
                    id=f"subdoc_{digest}",
                    element_ids=member_ids,
                    label=label,
                    source_hint=None,
                    confidence=confidence,
                    source=StructureSource.INFERRED,
                    metadata={
                        "grouping_rule": "repeated_explicit_title",
                        "title_element_id": title.id,
                    },
                )
            )
        return tuple(result)
