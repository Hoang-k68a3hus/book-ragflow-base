from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from rag.source_understanding.schemas.context import (
    Confidence,
    FiniteFloat,
    Identifier,
    JsonObject,
    Label,
    SchemaModel,
    StructureSource,
)
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.source_attributes import (
    HEADING_LEVEL_ATTRIBUTE,
    NUMBERING_FORMAT_ATTRIBUTE,
    NUMBERING_LEVEL_ATTRIBUTE,
    SourceAttributeError,
    source_heading_level,
    source_numbering_format,
    source_numbering_level,
    source_numbering_sequence_id,
    source_zone,
)

STRUCTURE_SIGNAL_VERSION = "3"
STRUCTURE_SIGNAL_POLICY_VERSION = "2"

_DEFAULT_SECTION_MARKERS = (
    "chapter",
    "section",
    "part",
    "điều",
    "khoản",
    "mục",
    "summary",
    "todo",
)
_DEFAULT_ORDERED_NUMBER_FORMATS = (
    "decimal",
    "upperRoman",
    "lowerRoman",
    "upperLetter",
    "lowerLetter",
)

_NUMBERING_RE = re.compile(r"^\s*(?P<marker>(?:\d+\.\d+(?:\.\d+)*|\d+[.)]|[A-Za-z][.)]|[IVXLCDMivxlcdm]+[.)]))\s+")
_NAVIGATION_ENTRY_RE = re.compile(r"^\s*\S.*\t+\s*\d+\s*$")
_QUESTION_RE = re.compile(r"^\s*(?P<marker>q(?:uestion)?\s*[:\-])\s*", re.IGNORECASE)
_ANSWER_RE = re.compile(r"^\s*(?P<marker>a(?:nswer)?\s*[:\-])\s*", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(
    r"^\s*(?P<marker>"
    r"\[(?:\d{4}-\d{2}-\d{2}[ T])?\d{1,2}:\d{2}(?::\d{2})?\]"
    r"|(?:\d{4}-\d{2}-\d{2}[ T])\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{1,2}:\d{2}(?::\d{2})?"
    r")(?=\s|$)"
)
_SPEAKER_RE = re.compile(r"^\s*(?P<marker>[^\s:][^:\n]{0,39})\s*:\s+(?=\S)")
_LEXICAL_TYPES = frozenset(
    {
        ElementType.TITLE,
        ElementType.HEADING,
        ElementType.PARAGRAPH,
        ElementType.SENTENCE,
        ElementType.LINE,
        ElementType.QUESTION,
        ElementType.ANSWER,
        ElementType.DIALOGUE_TURN,
        ElementType.UNKNOWN,
    }
)


class StructureSignalError(ValueError):
    """Input cannot be converted into trustworthy structural evidence."""


class StructureSignalKind(StrEnum):
    """Observed/inferred evidence only; no kind represents a final boundary."""

    ELEMENT_TYPE = "ELEMENT_TYPE"
    STYLE_BOLD = "STYLE_BOLD"
    STYLE_FONT_SIZE = "STYLE_FONT_SIZE"
    STYLE_INDENTATION = "STYLE_INDENTATION"
    HEADING_LEVEL = "HEADING_LEVEL"
    NUMBERING_LEVEL = "NUMBERING_LEVEL"
    NUMBERING_FORMAT = "NUMBERING_FORMAT"
    NUMBERING_MARKER = "NUMBERING_MARKER"
    OUTLINE_LEVEL = "OUTLINE_LEVEL"
    SECTION_MARKER = "SECTION_MARKER"
    QUESTION_MARKER = "QUESTION_MARKER"
    ANSWER_MARKER = "ANSWER_MARKER"
    TIMESTAMP_PATTERN = "TIMESTAMP_PATTERN"
    SPEAKER_LABEL_CANDIDATE = "SPEAKER_LABEL_CANDIDATE"
    ELEMENT_TYPE_TRANSITION = "ELEMENT_TYPE_TRANSITION"


class StructureSignalPolicy(SchemaModel):
    """Configuration that materially affects deterministic signal extraction."""

    version: str = STRUCTURE_SIGNAL_POLICY_VERSION
    section_markers: tuple[str, ...] = _DEFAULT_SECTION_MARKERS
    ordered_number_formats: tuple[str, ...] = _DEFAULT_ORDERED_NUMBER_FORMATS
    min_native_outline_sections: int = Field(default=5, ge=3, le=64)
    min_native_outline_children: int = Field(default=3, ge=1, le=256)
    min_preface_navigation_entries: int = Field(default=3, ge=2, le=64)
    min_preface_sections: int = Field(default=2, ge=2, le=16)
    max_outline_label_length: int = Field(default=160, ge=8, le=2048)
    outline_confidence: Confidence = 0.85

    @field_validator("section_markers", "ordered_number_formats", mode="before")
    @classmethod
    def normalize_string_sequences(cls, value: object) -> object:
        if isinstance(value, str) or value is None:
            raise ValueError("configured marker/format values must be a sequence of strings")
        if not isinstance(value, Iterable):
            raise ValueError("configured marker/format values must be iterable")  # noqa: TRY004 - Pydantic validator.
        items = tuple(value)
        if any(not isinstance(item, str) for item in items):
            raise ValueError("configured marker/format values must contain only strings")
        return tuple(item.strip() for item in items if isinstance(item, str))

    @model_validator(mode="after")
    def validate_configuration(self) -> StructureSignalPolicy:
        for name, values, max_length in (
            ("section_markers", self.section_markers, 128),
            ("ordered_number_formats", self.ordered_number_formats, 128),
        ):
            if not values or any(not value for value in values):
                raise ValueError(f"{name} must contain non-blank values")
            if any(len(value) > max_length for value in values):
                raise ValueError(f"{name} values must be <= {max_length} characters")
            folded = [value.casefold() for value in values]
            if len(folded) != len(set(folded)):
                raise ValueError(f"{name} must be unique case-insensitively")
        return self


class StructureSignal(SchemaModel):
    """One auditable piece of structural evidence attached to one/two elements."""

    id: Identifier
    kind: StructureSignalKind
    element_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=2)
    source: StructureSource
    confidence: Confidence | None = None
    text_value: Label | None = None
    numeric_value: FiniteFloat | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_element_refs(self) -> StructureSignal:
        if len(self.element_ids) != len(set(self.element_ids)):
            raise ValueError("structure signal element_ids must be unique")
        return self


class StructureSignalSet(SchemaModel):
    version: str = STRUCTURE_SIGNAL_VERSION
    element_count: int = Field(ge=1)
    policy: StructureSignalPolicy = Field(default_factory=StructureSignalPolicy)
    signals: tuple[StructureSignal, ...] = Field(default_factory=tuple)


class StructureSignalExtractor:
    """Extract auditable source/local evidence without deciding final structure."""

    version: str = STRUCTURE_SIGNAL_VERSION

    def __init__(
        self,
        *,
        section_markers: Sequence[str] | None = None,
        policy: StructureSignalPolicy | None = None,
    ) -> None:
        if section_markers is not None and policy is not None:
            raise ValueError("provide either section_markers or policy, not both")
        if policy is None:
            policy = StructureSignalPolicy(section_markers=(_DEFAULT_SECTION_MARKERS if section_markers is None else tuple(section_markers)))
        self._policy = policy
        self._section_markers = policy.section_markers
        escaped = "|".join(re.escape(marker) for marker in sorted(self._section_markers, key=len, reverse=True))
        self._section_marker_re = re.compile(
            rf"^\s*(?P<marker>{escaped})(?=\s|[:.\-]|$)",
            re.IGNORECASE,
        )

    def extract(self, elements: Sequence[Element]) -> StructureSignalSet:
        snapshot = tuple(elements)
        self._validate_elements(snapshot)

        signals: list[StructureSignal] = []
        for index, element in enumerate(snapshot):
            signals.extend(self._element_signals(element))
            if index:
                previous = snapshot[index - 1]
                if previous.type != element.type:
                    signals.append(
                        self._signal(
                            StructureSignalKind.ELEMENT_TYPE_TRANSITION,
                            (previous.id, element.id),
                            source=StructureSource.DERIVED,
                            metadata={
                                "from_type": previous.type.value,
                                "to_type": element.type.value,
                            },
                        )
                    )

        outline_signals = self._document_outline_signals(snapshot)
        if not outline_signals:
            outline_signals = self._preface_outline_signals(snapshot)
        signals.extend(outline_signals)

        return StructureSignalSet(
            element_count=len(snapshot),
            policy=self._policy,
            signals=tuple(signals),
        )

    def _element_signals(self, element: Element) -> list[StructureSignal]:
        signals = [
            self._signal(
                StructureSignalKind.ELEMENT_TYPE,
                (element.id,),
                source=element.provenance.source,
                confidence=element.confidence.type,
                text_value=element.type.value,
            )
        ]

        if element.type == ElementType.HEADING and HEADING_LEVEL_ATTRIBUTE in element.attributes:
            try:
                heading_level = source_heading_level(element)
            except SourceAttributeError as exc:
                raise StructureSignalError(str(exc)) from exc
            if heading_level is not None:
                signals.append(
                    self._signal(
                        StructureSignalKind.HEADING_LEVEL,
                        (element.id,),
                        source=element.provenance.source,
                        confidence=element.confidence.type,
                        numeric_value=float(heading_level),
                        metadata={"attribute_key": HEADING_LEVEL_ATTRIBUTE},
                    )
                )

        try:
            numbering_sequence_id = source_numbering_sequence_id(element)
            numbering_level = source_numbering_level(element)
            numbering_format = source_numbering_format(element)
        except SourceAttributeError as exc:
            raise StructureSignalError(str(exc)) from exc
        if element.type == ElementType.LIST_ITEM and numbering_level is not None:
            signals.append(
                self._signal(
                    StructureSignalKind.NUMBERING_LEVEL,
                    (element.id,),
                    source=element.provenance.source,
                    numeric_value=float(numbering_level),
                    metadata={
                        "attribute_key": NUMBERING_LEVEL_ATTRIBUTE,
                        "numbering_sequence_id": numbering_sequence_id,
                    },
                )
            )
        if element.type == ElementType.LIST_ITEM and numbering_format is not None:
            signals.append(
                self._signal(
                    StructureSignalKind.NUMBERING_FORMAT,
                    (element.id,),
                    source=element.provenance.source,
                    text_value=numbering_format,
                    metadata={
                        "attribute_key": NUMBERING_FORMAT_ATTRIBUTE,
                        "numbering_sequence_id": numbering_sequence_id,
                    },
                )
            )

        if element.style is not None:
            if element.style.bold is True:
                signals.append(
                    self._signal(
                        StructureSignalKind.STYLE_BOLD,
                        (element.id,),
                        source=element.provenance.source,
                    )
                )
            if element.style.font_size is not None:
                signals.append(
                    self._signal(
                        StructureSignalKind.STYLE_FONT_SIZE,
                        (element.id,),
                        source=element.provenance.source,
                        numeric_value=element.style.font_size,
                    )
                )
            if element.style.indentation is not None:
                signals.append(
                    self._signal(
                        StructureSignalKind.STYLE_INDENTATION,
                        (element.id,),
                        source=element.provenance.source,
                        numeric_value=element.style.indentation,
                    )
                )

        text = element.text
        if text is None or element.type not in _LEXICAL_TYPES:
            return signals

        question_match = _QUESTION_RE.match(text)
        answer_match = _ANSWER_RE.match(text)
        timestamp_match = _TIMESTAMP_RE.match(text)

        for kind, match in (
            (StructureSignalKind.NUMBERING_MARKER, _NUMBERING_RE.match(text)),
            (StructureSignalKind.SECTION_MARKER, self._section_marker_re.match(text)),
            (StructureSignalKind.QUESTION_MARKER, question_match),
            (StructureSignalKind.ANSWER_MARKER, answer_match),
            (StructureSignalKind.TIMESTAMP_PATTERN, timestamp_match),
        ):
            if match is not None:
                signals.append(
                    self._signal(
                        kind,
                        (element.id,),
                        source=StructureSource.INFERRED,
                        text_value=match.group("marker").strip(),
                    )
                )

        speaker_match = _SPEAKER_RE.match(text)
        if speaker_match is not None and question_match is None and answer_match is None and timestamp_match is None and self._section_marker_re.match(text) is None:
            signals.append(
                self._signal(
                    StructureSignalKind.SPEAKER_LABEL_CANDIDATE,
                    (element.id,),
                    source=StructureSource.INFERRED,
                    text_value=speaker_match.group("marker").strip(),
                )
            )

        return signals

    def _document_outline_signals(
        self,
        elements: tuple[Element, ...],
    ) -> list[StructureSignal]:
        """Infer a repeated native ordered outline without rewriting source facts."""

        groups: dict[tuple[str | None, str, str], list[tuple[Element, int]]] = defaultdict(list)
        for element in elements:
            if element.type != ElementType.LIST_ITEM:
                continue
            try:
                sequence_id = source_numbering_sequence_id(element)
                level = source_numbering_level(element)
                number_format = source_numbering_format(element)
                zone = source_zone(element)
            except SourceAttributeError as exc:
                raise StructureSignalError(str(exc)) from exc
            if sequence_id is None or level is None or number_format is None:
                continue
            groups[(zone, sequence_id, number_format)].append((element, level))

        ordered_formats = {value.casefold() for value in self._policy.ordered_number_formats}
        qualified: list[tuple[tuple[str | None, str, str], list[Element], list[tuple[Element, int]]]] = []
        for key, members in groups.items():
            if key[2].casefold() not in ordered_formats:
                continue
            top_level = [
                element
                for element, level in members
                if level == 0 and element.style is not None and element.style.bold is True and element.text is not None and 0 < len(element.text.strip()) <= self._policy.max_outline_label_length
            ]
            nested_count = sum(level > 0 for _, level in members)
            if len(top_level) >= self._policy.min_native_outline_sections and nested_count >= self._policy.min_native_outline_children:
                top_level.sort(key=lambda item: item.order)
                qualified.append((key, top_level, members))

        if len(qualified) != 1:
            return []

        (zone, sequence_id, number_format), sections, _ = qualified[0]
        first_section = sections[0]
        last_section = sections[-1]
        leading_headings = [element for element in elements if element.type == ElementType.HEADING and element.order < first_section.order and self._safe_zone(element) == zone]
        if len(leading_headings) != 1 or leading_headings[0].order != 0:
            return []
        root = leading_headings[0]

        pre_labels = [element for element in elements if root.order < element.order < first_section.order and self._is_outline_paragraph_label(element, zone=zone, require_colon=True)]
        section_level = 2 if pre_labels else 1
        output = [
            self._outline_signal(
                root,
                level=0,
                role="DOCUMENT_TITLE",
                sequence_id=sequence_id,
                number_format=number_format,
                section_count=len(sections),
            )
        ]
        output.extend(
            self._outline_signal(
                element,
                level=1,
                role="SECTION",
                sequence_id=sequence_id,
                number_format=number_format,
                section_count=len(sections),
            )
            for element in pre_labels
        )
        output.extend(
            self._outline_signal(
                element,
                level=section_level,
                role="NUMBERED_SECTION",
                sequence_id=sequence_id,
                number_format=number_format,
                section_count=len(sections),
                native_numbering_level=0,
            )
            for element in sections
        )

        post_label = self._post_outline_label(
            elements,
            after_order=last_section.order,
            zone=zone,
            sequence_id=sequence_id,
        )
        if post_label is not None:
            output.append(
                self._outline_signal(
                    post_label,
                    level=1,
                    role="SECTION",
                    sequence_id=sequence_id,
                    number_format=number_format,
                    section_count=len(sections),
                )
            )
        return output

    def _preface_outline_signals(
        self,
        elements: tuple[Element, ...],
    ) -> list[StructureSignal]:
        """Infer a styled preface root/sections only when a TOC corroborates them."""

        headings = [element for element in elements if element.type == ElementType.HEADING and (element.text or "").strip()]
        if not headings:
            return []
        first_heading = min(headings, key=lambda item: item.order)
        if first_heading.order <= 1:
            return []
        try:
            first_heading_level = source_heading_level(first_heading)
            zone = source_zone(first_heading)
        except SourceAttributeError as exc:
            raise StructureSignalError(str(exc)) from exc
        if first_heading_level != 1:
            return []

        navigation_entries = [
            element for element in elements if element.order < first_heading.order and self._safe_zone(element) == zone and element.text is not None and _NAVIGATION_ENTRY_RE.match(element.text)
        ]
        if len(navigation_entries) < self._policy.min_preface_navigation_entries:
            return []
        navigation_start = min(item.order for item in navigation_entries)

        visible_before_navigation = [element for element in elements if element.order < navigation_start and self._safe_zone(element) == zone and element.text is not None and element.text.strip()]
        if not visible_before_navigation:
            return []

        styled = [
            element
            for element in visible_before_navigation
            if element.type == ElementType.PARAGRAPH
            and element.style is not None
            and element.style.bold is True
            and element.style.font_size is not None
            and 0 < len((element.text or "").strip()) <= self._policy.max_outline_label_length
        ]
        if len(styled) < 1 + self._policy.min_preface_sections:
            return []

        max_size = max(element.style.font_size for element in styled if element.style is not None and element.style.font_size is not None)
        title_candidates = [element for element in styled if element.style is not None and element.style.font_size == max_size]
        if len(title_candidates) != 1:
            return []
        title = title_candidates[0]
        if title.order != visible_before_navigation[0].order:
            return []

        by_size: dict[float, list[Element]] = defaultdict(list)
        for element in styled:
            if element.id == title.id or element.order <= title.order or element.style is None:
                continue
            size = element.style.font_size
            if size is not None and size < max_size:
                by_size[size].append(element)
        if not by_size:
            return []
        best_count = max(len(items) for items in by_size.values())
        best_sizes = [size for size, items in by_size.items() if len(items) == best_count]
        if len(best_sizes) != 1 or best_count < self._policy.min_preface_sections:
            return []
        section_size = best_sizes[0]
        sections = sorted(by_size[section_size], key=lambda item: item.order)

        output = [
            self._outline_signal(
                title,
                level=0,
                role="DOCUMENT_TITLE",
                sequence_id="preface-typography",
                number_format="none",
                section_count=len(sections),
                detection_rule="styled_preface_with_navigation",
            )
        ]
        output.extend(
            self._outline_signal(
                section,
                level=1,
                role="SECTION",
                sequence_id="preface-typography",
                number_format="none",
                section_count=len(sections),
                detection_rule="styled_preface_with_navigation",
            )
            for section in sections
        )
        return output

    def _post_outline_label(
        self,
        elements: tuple[Element, ...],
        *,
        after_order: int,
        zone: str | None,
        sequence_id: str,
    ) -> Element | None:
        candidates: list[Element] = []
        found_table = False
        for element in elements:
            if element.order <= after_order or self._safe_zone(element) != zone:
                continue
            if element.type == ElementType.HEADING:
                return None
            if element.type == ElementType.TABLE:
                found_table = True
                break
            if element.type == ElementType.LIST_ITEM:
                try:
                    current_sequence = source_numbering_sequence_id(element)
                except SourceAttributeError as exc:
                    raise StructureSignalError(str(exc)) from exc
                if current_sequence != sequence_id:
                    return None
                continue
            if self._is_outline_paragraph_label(element, zone=zone, require_colon=False):
                candidates.append(element)
        if not found_table or len(candidates) != 1:
            return None
        return candidates[0]

    def _is_outline_paragraph_label(
        self,
        element: Element,
        *,
        zone: str | None,
        require_colon: bool,
    ) -> bool:
        if element.type != ElementType.PARAGRAPH or self._safe_zone(element) != zone:
            return False
        text = (element.text or "").strip()
        if not text or len(text) > self._policy.max_outline_label_length:
            return False
        if require_colon and not text.endswith(":"):
            return False
        style = element.style
        return style is not None and style.bold is True and style.indentation is not None

    def _outline_signal(
        self,
        element: Element,
        *,
        level: int,
        role: str,
        sequence_id: str,
        number_format: str,
        section_count: int,
        native_numbering_level: int | None = None,
        detection_rule: str = "repeated_native_ordered_outline",
    ) -> StructureSignal:
        metadata: dict[str, object] = {
            "context_role": role,
            "detection_rule": detection_rule,
            "numbering_sequence_id": sequence_id,
            "numbering_format": number_format,
            "top_level_section_count": section_count,
        }
        if native_numbering_level is not None:
            metadata["native_numbering_level"] = native_numbering_level
        return self._signal(
            StructureSignalKind.OUTLINE_LEVEL,
            (element.id,),
            source=StructureSource.INFERRED,
            confidence=self._policy.outline_confidence,
            numeric_value=float(level),
            metadata=metadata,
        )

    @staticmethod
    def _safe_zone(element: Element) -> str | None:
        try:
            return source_zone(element)
        except SourceAttributeError as exc:
            raise StructureSignalError(str(exc)) from exc

    @staticmethod
    def _signal(
        kind: StructureSignalKind,
        element_ids: tuple[str, ...],
        *,
        source: StructureSource,
        confidence: float | None = None,
        text_value: str | None = None,
        numeric_value: float | None = None,
        metadata: dict[str, object] | None = None,
    ) -> StructureSignal:
        identity = "|".join((kind.value, *element_ids))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return StructureSignal(
            id=f"sig_{digest}",
            kind=kind,
            element_ids=element_ids,
            source=source,
            confidence=confidence,
            text_value=text_value,
            numeric_value=numeric_value,
            metadata={} if metadata is None else metadata,
        )

    @staticmethod
    def _validate_elements(elements: tuple[Element, ...]) -> None:
        if not elements:
            raise StructureSignalError("cannot extract structure signals from empty elements")

        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise StructureSignalError("structure signals require unique element ids")

        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise StructureSignalError("structure signals require unique element order values")
        if orders != sorted(orders):
            raise StructureSignalError("structure signals require elements in ascending canonical source order")
