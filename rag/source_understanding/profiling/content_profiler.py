from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from rag.source_understanding.schemas.context import Confidence, ConfidenceMap, SchemaModel
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.source_attributes import SourceAttributeError, source_zone

CONTENT_PROFILER_VERSION = "2"


class ContentProfilingError(ValueError):
    """Canonical elements cannot be profiled without violating input invariants."""


class ContentCategory(StrEnum):
    """Coarse observed content categories used for routing, not document types."""

    NARRATIVE = "narrative"
    LIST = "list"
    DIALOGUE = "dialogue"
    CODE = "code"
    TABLE = "table"
    QA = "qa"
    FORMULA = "formula"
    LOG = "log"
    KEY_VALUE = "key_value"
    VISUAL = "visual"
    BOILERPLATE = "boilerplate"
    SEPARATOR = "separator"
    UNKNOWN = "unknown"


_ELEMENT_CATEGORY: dict[ElementType, ContentCategory] = {
    ElementType.TITLE: ContentCategory.NARRATIVE,
    ElementType.HEADING: ContentCategory.NARRATIVE,
    ElementType.PARAGRAPH: ContentCategory.NARRATIVE,
    ElementType.SENTENCE: ContentCategory.NARRATIVE,
    ElementType.LINE: ContentCategory.NARRATIVE,
    ElementType.LIST: ContentCategory.LIST,
    ElementType.LIST_ITEM: ContentCategory.LIST,
    ElementType.TABLE: ContentCategory.TABLE,
    ElementType.TABLE_ROW: ContentCategory.TABLE,
    ElementType.TABLE_CELL: ContentCategory.TABLE,
    ElementType.CODE: ContentCategory.CODE,
    ElementType.FORMULA: ContentCategory.FORMULA,
    ElementType.QUESTION: ContentCategory.QA,
    ElementType.ANSWER: ContentCategory.QA,
    ElementType.DIALOGUE_TURN: ContentCategory.DIALOGUE,
    ElementType.LOG_ENTRY: ContentCategory.LOG,
    ElementType.KEY_VALUE: ContentCategory.KEY_VALUE,
    ElementType.FIGURE: ContentCategory.VISUAL,
    ElementType.CHART: ContentCategory.VISUAL,
    ElementType.CAPTION: ContentCategory.NARRATIVE,
    ElementType.FOOTNOTE: ContentCategory.NARRATIVE,
    ElementType.SEPARATOR: ContentCategory.SEPARATOR,
    ElementType.HEADER: ContentCategory.BOILERPLATE,
    ElementType.FOOTER: ContentCategory.BOILERPLATE,
    ElementType.PAGE_NUMBER: ContentCategory.BOILERPLATE,
    ElementType.UNKNOWN: ContentCategory.UNKNOWN,
}


def content_category_for_type(element_type: ElementType) -> ContentCategory:
    """Return the stable coarse routing category for a canonical element type."""

    try:
        return _ELEMENT_CATEGORY[element_type]
    except KeyError as exc:  # pragma: no cover - protects future enum expansion.
        raise ContentProfilingError(f"element type {element_type.value!r} has no registered content category") from exc


def content_category_for_element(element: Element) -> ContentCategory:
    """Return routing category while respecting explicit source-zone facts.

    Header/footer material remains typed (TABLE, FIGURE, CODE, ...) so no source
    structure is lost, but it routes as boilerplate instead of changing the main
    document modality merely because a header contains a layout table or logo.
    """

    try:
        zone = source_zone(element)
    except SourceAttributeError as exc:
        raise ContentProfilingError(str(exc)) from exc
    if zone is not None and zone.casefold() in {"header", "footer"}:
        return ContentCategory.BOILERPLATE
    return content_category_for_type(element.type)


class ContentProfileSignals(SchemaModel):
    """Directly observed counts/ratios; no lexical or semantic inference."""

    title_count: int = Field(ge=0)
    heading_count: int = Field(ge=0)
    list_count: int = Field(ge=0)
    list_item_count: int = Field(ge=0)
    speaker_turn_count: int = Field(ge=0)
    code_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    table_row_count: int = Field(ge=0)
    table_cell_count: int = Field(ge=0)
    question_count: int = Field(ge=0)
    answer_count: int = Field(ge=0)
    formula_count: int = Field(ge=0)
    log_entry_count: int = Field(ge=0)
    key_value_count: int = Field(ge=0)
    visual_count: int = Field(ge=0)
    separator_count: int = Field(ge=0)
    boilerplate_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    text_present_count: int = Field(ge=0)
    located_element_count: int = Field(ge=0)
    styled_element_count: int = Field(ge=0)
    excluded_from_retrieval_count: int = Field(ge=0)
    category_switch_count: int = Field(ge=0)
    typed_element_ratio: Confidence
    text_present_ratio: Confidence
    category_switch_ratio: Confidence


class ContentProfile(SchemaModel):
    """Deterministic element-frequency profile for downstream structure analysis.

    Distribution values describe canonical element representation frequency. They
    are not token/character mass and must not be treated as a single document type.
    """

    version: str = CONTENT_PROFILER_VERSION
    element_count: int = Field(ge=1)
    element_type_distribution: ConfidenceMap
    category_distribution: ConfidenceMap
    dominant_category: ContentCategory | None = None
    dominant_share: Confidence | None = None
    signals: ContentProfileSignals


class ContentProfiler:
    """Measure observed content composition without inferring document structure."""

    version: str = CONTENT_PROFILER_VERSION

    def analyze(self, elements: Sequence[Element]) -> ContentProfile:
        snapshot = tuple(elements)
        self._validate_elements(snapshot)

        type_counts: Counter[ElementType] = Counter(element.type for element in snapshot)
        categories = tuple(content_category_for_element(element) for element in snapshot)
        category_counts: Counter[ContentCategory] = Counter(categories)
        element_count = len(snapshot)

        type_distribution = {element_type.value: type_counts[element_type] / element_count for element_type in ElementType}
        category_distribution = {category.value: category_counts[category] / element_count for category in ContentCategory}

        dominant_category, dominant_share = self._dominant_category(category_counts, element_count)
        category_switch_count = sum(previous != current for previous, current in itertools.pairwise(categories))
        category_switch_ratio = category_switch_count / (element_count - 1) if element_count > 1 else 0.0

        unknown_count = type_counts[ElementType.UNKNOWN]
        text_present_count = sum(element.text is not None for element in snapshot)

        signals = ContentProfileSignals(
            title_count=type_counts[ElementType.TITLE],
            heading_count=type_counts[ElementType.HEADING],
            list_count=type_counts[ElementType.LIST],
            list_item_count=type_counts[ElementType.LIST_ITEM],
            speaker_turn_count=type_counts[ElementType.DIALOGUE_TURN],
            code_count=type_counts[ElementType.CODE],
            table_count=type_counts[ElementType.TABLE],
            table_row_count=type_counts[ElementType.TABLE_ROW],
            table_cell_count=type_counts[ElementType.TABLE_CELL],
            question_count=type_counts[ElementType.QUESTION],
            answer_count=type_counts[ElementType.ANSWER],
            formula_count=type_counts[ElementType.FORMULA],
            log_entry_count=type_counts[ElementType.LOG_ENTRY],
            key_value_count=type_counts[ElementType.KEY_VALUE],
            visual_count=type_counts[ElementType.FIGURE] + type_counts[ElementType.CHART],
            separator_count=type_counts[ElementType.SEPARATOR],
            boilerplate_count=category_counts[ContentCategory.BOILERPLATE],
            unknown_count=unknown_count,
            text_present_count=text_present_count,
            located_element_count=sum(element.location is not None for element in snapshot),
            styled_element_count=sum(element.style is not None for element in snapshot),
            excluded_from_retrieval_count=sum(element.exclude_from_retrieval for element in snapshot),
            category_switch_count=category_switch_count,
            typed_element_ratio=(element_count - unknown_count) / element_count,
            text_present_ratio=text_present_count / element_count,
            category_switch_ratio=category_switch_ratio,
        )

        return ContentProfile(
            element_count=element_count,
            element_type_distribution=type_distribution,
            category_distribution=category_distribution,
            dominant_category=dominant_category,
            dominant_share=dominant_share,
            signals=signals,
        )

    @staticmethod
    def _category_for_type(element_type: ElementType) -> ContentCategory:
        """Compatibility shim for callers using the older private helper."""

        return content_category_for_type(element_type)

    @staticmethod
    def _dominant_category(
        counts: Counter[ContentCategory],
        element_count: int,
    ) -> tuple[ContentCategory | None, float | None]:
        highest = max(counts.values())
        leaders = [category for category, count in counts.items() if count == highest]
        if len(leaders) != 1:
            return None, None
        return leaders[0], highest / element_count

    @staticmethod
    def _validate_elements(elements: tuple[Element, ...]) -> None:
        if not elements:
            raise ContentProfilingError("cannot profile an empty element sequence")

        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise ContentProfilingError("content profiler requires unique element ids")

        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise ContentProfilingError("content profiler requires unique element order values")
        if orders != sorted(orders):
            raise ContentProfilingError("content profiler requires elements in ascending canonical source order")
