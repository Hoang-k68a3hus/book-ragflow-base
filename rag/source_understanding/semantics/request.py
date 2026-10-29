from __future__ import annotations

from collections.abc import Iterable

from rag.source_understanding.schemas.context import JsonObject
from rag.source_understanding.schemas.document import SemanticTextView
from rag.source_understanding.schemas.element import Element

from .provider import (
    SemanticRequest,
    SemanticRequestSegment,
    SemanticTargetKind,
)

SEMANTIC_REQUEST_BUILDER_VERSION = "3"


class SemanticContextPlanner:
    """Select bounded, source-near context without widening evidence scope.

    Context is supplied to the provider as inference context only.  The
    annotator still validates candidate evidence against target segments.
    When a logical-unit scope is supplied, neighbors are limited to that
    inferred unit; otherwise canonical source order is used as a conservative
    fallback.
    """

    version = "1"

    def __init__(self, *, max_context_elements: int = 2) -> None:
        if max_context_elements < 0 or max_context_elements > 8:
            raise ValueError("max_context_elements must be in [0, 8]")
        self._max_context_elements = max_context_elements

    def plan(
        self,
        *,
        target_elements: tuple[Element, ...],
        source_elements: tuple[Element, ...],
        scope_element_ids: frozenset[str] | None = None,
    ) -> tuple[Element, ...]:
        if self._max_context_elements == 0 or not target_elements:
            return ()
        target_ids = {element.id for element in target_elements}
        allowed_ids = scope_element_ids
        ordered = tuple(element for element in source_elements if element.id not in target_ids and (allowed_ids is None or element.id in allowed_ids))
        if not ordered:
            return ()
        positions = {element.id: index for index, element in enumerate(source_elements)}
        target_positions = sorted(positions[element.id] for element in target_elements if element.id in positions)
        if not target_positions:
            return ()
        first = target_positions[0]
        last = target_positions[-1]
        previous = [element for element in reversed(source_elements[:first]) if element.id not in target_ids and (allowed_ids is None or element.id in allowed_ids)]
        following = [element for element in source_elements[last + 1 :] if element.id not in target_ids and (allowed_ids is None or element.id in allowed_ids)]
        selected: list[Element] = []
        while len(selected) < self._max_context_elements and (previous or following):
            if previous:
                selected.append(previous.pop(0))
            if len(selected) >= self._max_context_elements:
                break
            if following:
                selected.append(following.pop(0))
        return tuple(sorted(selected, key=lambda element: element.order))


class SemanticRequestBuilder:
    """Build provider text without losing its reversible Element mapping."""

    version = SEMANTIC_REQUEST_BUILDER_VERSION

    def __init__(
        self,
        *,
        max_request_chars: int,
        text_separator: str,
        text_view_preference: tuple[SemanticTextView, ...],
    ) -> None:
        if max_request_chars < 1 or max_request_chars > 32768:
            raise ValueError("max_request_chars must be in [1, 32768]")
        if not text_separator:
            raise ValueError("text_separator must not be empty")
        if not text_view_preference:
            raise ValueError("text_view_preference must not be empty")
        if len(text_view_preference) != len(set(text_view_preference)):
            raise ValueError("text_view_preference must be unique")
        self._max_request_chars = max_request_chars
        self._text_separator = text_separator
        self._text_view_preference = text_view_preference

    def build(
        self,
        *,
        target_id: str,
        target_kind: SemanticTargetKind,
        target_elements: tuple[Element, ...],
        language: str | None,
        logical_unit_type: str | None,
        unit_label: str | None,
        context_labels: tuple[str, ...],
        metadata: JsonObject,
        context_elements: tuple[Element, ...] = (),
    ) -> SemanticRequest | None:
        if not target_elements:
            return None
        element_ids = tuple(element.id for element in target_elements)
        chunks: list[str] = []
        target_segments: list[SemanticRequestSegment] = []
        context_segments: list[SemanticRequestSegment] = []
        omitted_target_ids: list[str] = []
        omitted_context_ids: list[str] = []
        truncated_ids: list[str] = []

        self._append_elements(
            target_elements,
            chunks=chunks,
            segments=target_segments,
            omitted_ids=omitted_target_ids,
            truncated_ids=truncated_ids,
            allow_first_segment_truncation=True,
        )
        if not target_segments:
            return None

        target_id_set = set(element_ids)
        unique_context_elements = tuple(element for element in context_elements if element.id not in target_id_set)
        self._append_elements(
            unique_context_elements,
            chunks=chunks,
            segments=context_segments,
            omitted_ids=omitted_context_ids,
            truncated_ids=truncated_ids,
            allow_first_segment_truncation=False,
        )

        request_metadata = dict(metadata)
        request_metadata.update(
            {
                "semantic_request_builder_version": self.version,
                "request_truncated": bool(omitted_target_ids or omitted_context_ids or truncated_ids),
                "truncated_element_ids": truncated_ids,
                "omitted_target_element_ids": omitted_target_ids,
                "omitted_context_element_ids": omitted_context_ids,
            }
        )
        return SemanticRequest(
            target_id=target_id,
            target_kind=target_kind,
            text="".join(chunks),
            language=language,
            element_ids=element_ids,
            target_segments=tuple(target_segments),
            context_segments=tuple(context_segments),
            logical_unit_type=logical_unit_type,
            unit_label=unit_label,
            context_labels=context_labels,
            metadata=request_metadata,
        )

    def _append_elements(
        self,
        elements: Iterable[Element],
        *,
        chunks: list[str],
        segments: list[SemanticRequestSegment],
        omitted_ids: list[str],
        truncated_ids: list[str],
        allow_first_segment_truncation: bool,
    ) -> None:
        text_bearing = tuple((element, selected) for element in elements if (selected := self._element_text(element)) is not None)
        for index, (element, selected) in enumerate(text_bearing):
            text, text_view = selected
            separator = self._text_separator if chunks else ""
            available = self._max_request_chars - sum(map(len, chunks)) - len(separator)
            if available <= 0:
                omitted_ids.extend(item.id for item, _ in text_bearing[index:])
                return
            if len(text) > available:
                if segments or not allow_first_segment_truncation:
                    omitted_ids.extend(item.id for item, _ in text_bearing[index:])
                    return
                segment_text = text[:available]
                if not segment_text.strip():
                    omitted_ids.extend(item.id for item, _ in text_bearing[index:])
                    return
                truncated_ids.append(element.id)
            else:
                segment_text = text

            if separator:
                chunks.append(separator)
            request_start = sum(map(len, chunks))
            chunks.append(segment_text)
            request_end = request_start + len(segment_text)
            segments.append(
                SemanticRequestSegment(
                    element_id=element.id,
                    text=segment_text,
                    text_view=text_view,
                    element_start=0,
                    element_end=len(segment_text),
                    request_start=request_start,
                    request_end=request_end,
                )
            )
            if len(segment_text) < len(text):
                omitted_ids.extend(item.id for item, _ in text_bearing[index + 1 :])
                return

    def _element_text(self, element: Element) -> tuple[str, SemanticTextView] | None:
        for text_view in self._text_view_preference:
            value = element.raw_text if text_view == SemanticTextView.RAW_TEXT else element.normalized_text
            if value is not None and value.strip():
                return value, text_view
        return None
