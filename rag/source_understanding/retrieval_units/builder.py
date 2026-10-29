from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from enum import StrEnum

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import (
    ContextNode,
    ContextNodeRef,
    Identifier,
    SchemaModel,
    StructureMode,
)
from rag.source_understanding.schemas.document import CanonicalDocument, ContentRegion, SubDocument
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.schemas.logical_unit import LogicalUnit, LogicalUnitType
from rag.source_understanding.schemas.retrieval_unit import RetrievalUnit, RetrievalUnitType, SourceAnchor
from rag.source_understanding.source_attributes import SourceAttributeError, source_zone

RETRIEVAL_UNIT_BUILDER_VERSION = "3"


class RetrievalUnitBuildError(ValueError):
    """A canonical document cannot be projected into trustworthy retrieval units."""


class RetrievalStrategy(StrEnum):
    FLAT = "FLAT"
    LOCAL = "LOCAL"
    GROUPED = "GROUPED"
    HIERARCHICAL = "HIERARCHICAL"
    MIXED = "MIXED"


class RetrievalUnitBuildPolicy(SchemaModel):
    """Projection policy; token targets are retrieval policy, never source structure."""

    version: str = Field(
        default=RETRIEVAL_UNIT_BUILDER_VERSION,
        min_length=1,
        max_length=128,
    )
    max_tokens: int | None = Field(default=None, ge=1)
    adaptive_text_partitioning: bool = True
    include_document_title: bool = True
    include_context_labels: bool = True
    exclude_page_numbers: bool = True
    exclude_boilerplate_source_zones: tuple[str, ...] = ("header", "footer")
    display_separator: str = "\n\n"
    retrieval_separator: str = "\n"
    context_separator: str = " > "

    @model_validator(mode="after")
    def validate_policy(self) -> RetrievalUnitBuildPolicy:
        for name in ("display_separator", "retrieval_separator", "context_separator"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")
        normalized_zones: list[str] = []
        for zone in self.exclude_boilerplate_source_zones:
            if not zone or zone.strip() != zone or len(zone) > 128:
                raise ValueError("exclude_boilerplate_source_zones must contain trimmed non-blank values <= 128 chars")
            normalized_zones.append(zone.casefold())
        if len(normalized_zones) != len(set(normalized_zones)):
            raise ValueError("exclude_boilerplate_source_zones must be unique case-insensitively")
        return self


class RetrievalUnitBuildResult(SchemaModel):
    version: str = RETRIEVAL_UNIT_BUILDER_VERSION
    document_id: Identifier
    strategy: RetrievalStrategy
    source_element_count: int = Field(ge=0)
    retrievable_element_count: int = Field(ge=0)
    covered_element_count: int = Field(ge=0)
    policy: RetrievalUnitBuildPolicy
    units: tuple[RetrievalUnit, ...] = Field(default_factory=tuple)
    oversized_unit_ids: tuple[str, ...] = Field(default_factory=tuple)
    partitioned_logical_unit_ids: tuple[str, ...] = Field(default_factory=tuple)
    skipped_excluded_element_ids: tuple[str, ...] = Field(default_factory=tuple)
    skipped_blank_element_ids: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_integrity_element_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_diagnostics(self) -> RetrievalUnitBuildResult:
        unit_ids = [unit.id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("retrieval unit ids must be unique")
        if len(self.oversized_unit_ids) != len(set(self.oversized_unit_ids)):
            raise ValueError("oversized_unit_ids must be unique")
        if set(self.oversized_unit_ids) - set(unit_ids):
            raise ValueError("oversized_unit_ids must reference emitted retrieval units")
        if len(self.partitioned_logical_unit_ids) != len(set(self.partitioned_logical_unit_ids)):
            raise ValueError("partitioned_logical_unit_ids must be unique")
        return self


_LOGICAL_TO_RETRIEVAL_TYPE: dict[LogicalUnitType, RetrievalUnitType] = {
    LogicalUnitType.TEXT_BLOCK: RetrievalUnitType.TEXT,
    LogicalUnitType.SECTION: RetrievalUnitType.SECTION,
    LogicalUnitType.TOPIC_GROUP: RetrievalUnitType.MIXED,
    LogicalUnitType.QA_PAIR: RetrievalUnitType.QA_PAIR,
    LogicalUnitType.DIALOGUE_SEGMENT: RetrievalUnitType.DIALOGUE,
    LogicalUnitType.CODE_BLOCK: RetrievalUnitType.CODE,
    LogicalUnitType.TABLE_BLOCK: RetrievalUnitType.TABLE,
    LogicalUnitType.LOG_WINDOW: RetrievalUnitType.LOG,
    LogicalUnitType.KEY_VALUE_GROUP: RetrievalUnitType.TEXT,
    LogicalUnitType.LIST_GROUP: RetrievalUnitType.LIST,
    LogicalUnitType.UNKNOWN_GROUP: RetrievalUnitType.UNKNOWN,
}

_ELEMENT_TO_RETRIEVAL_TYPE: dict[ElementType, RetrievalUnitType] = {
    ElementType.TITLE: RetrievalUnitType.SECTION,
    ElementType.HEADING: RetrievalUnitType.SECTION,
    ElementType.CODE: RetrievalUnitType.CODE,
    ElementType.TABLE: RetrievalUnitType.TABLE,
    ElementType.LIST: RetrievalUnitType.LIST,
    ElementType.DIALOGUE_TURN: RetrievalUnitType.DIALOGUE,
    ElementType.LOG_ENTRY: RetrievalUnitType.LOG,
    ElementType.UNKNOWN: RetrievalUnitType.UNKNOWN,
}

# These elements are deliberately left unresolved by structure grouping when
# continuity/integrity is unknown. Emitting singletons would silently destroy
# the very integrity signal that the structural layer preserved.
_UNSAFE_SINGLETON_TYPES = frozenset(
    {
        ElementType.TABLE_ROW,
        ElementType.TABLE_CELL,
        ElementType.LIST_ITEM,
        ElementType.FORMULA,
        ElementType.FIGURE,
        ElementType.CHART,
    }
)


class RetrievalUnitBuilder:
    """Build deterministic, provenance-safe RetrievalUnits from CanonicalDocument."""

    version: str = RETRIEVAL_UNIT_BUILDER_VERSION

    def __init__(
        self,
        token_counter: Callable[[str], int],
        policy: RetrievalUnitBuildPolicy | None = None,
    ) -> None:
        if not callable(token_counter):
            raise TypeError("token_counter must be callable")
        self._token_counter = token_counter
        self._policy = policy if policy is not None else RetrievalUnitBuildPolicy()
        self._excluded_zones = frozenset(zone.casefold() for zone in self._policy.exclude_boilerplate_source_zones)

    def build(self, document: CanonicalDocument) -> RetrievalUnitBuildResult:
        elements = tuple(document.elements)
        by_id = {element.id: element for element in elements}
        order = {element.id: index for index, element in enumerate(elements)}
        nodes = {node.id: node for node in document.context_nodes}
        regions = {region.id: region for region in document.regions}
        subdocuments = tuple(document.subdocuments)

        self._validate_logical_unit_ownership(document.logical_units)

        emitted: list[RetrievalUnit] = []
        covered: set[str] = set()
        oversized: list[str] = []
        skipped_blank: list[str] = []
        unresolved_integrity: list[str] = []
        partitioned_logical_units: list[str] = []

        units = sorted(
            document.logical_units,
            key=lambda unit: order[unit.element_ids[0]],
        )
        for logical_unit in units:
            members = tuple(by_id[element_id] for element_id in logical_unit.element_ids)
            excluded = tuple(element.id for element in members if self._excluded_from_retrieval(element))
            if excluded:
                if len(excluded) != len(members):
                    raise RetrievalUnitBuildError(f"logical unit {logical_unit.id!r} mixes retrieval-excluded and retrievable elements; refusing to break logical-unit integrity")
                covered.update(element.id for element in members)
                continue

            retrieval_units = self._from_logical_unit(
                document,
                logical_unit,
                members,
                nodes,
                regions,
                subdocuments,
            )
            if not retrieval_units:
                skipped_blank.extend(element.id for element in members)
                covered.update(element.id for element in members)
                continue

            if len(retrieval_units) > 1:
                partitioned_logical_units.append(logical_unit.id)
            for retrieval_unit in retrieval_units:
                emitted.append(retrieval_unit)
                covered.update(retrieval_unit.element_ids)
                if self._is_oversized(retrieval_unit):
                    oversized.append(retrieval_unit.id)

        for element in elements:
            if element.id in covered or self._excluded_from_retrieval(element):
                continue
            if element.type in _UNSAFE_SINGLETON_TYPES:
                unresolved_integrity.append(element.id)
                covered.add(element.id)
                continue

            retrieval_unit = self._from_single_element(
                document,
                element,
                nodes,
                regions,
                subdocuments,
            )
            covered.add(element.id)
            if retrieval_unit is None:
                skipped_blank.append(element.id)
                continue
            emitted.append(retrieval_unit)
            if self._is_oversized(retrieval_unit):
                oversized.append(retrieval_unit.id)

        emitted.sort(key=lambda unit: order[unit.element_ids[0]])
        for unit in emitted:
            try:
                unit.validate_against_document(document)
            except ValueError as exc:
                raise RetrievalUnitBuildError(f"retrieval unit {unit.id!r} failed canonical validation: {exc}") from exc

        excluded_ids = tuple(element.id for element in elements if self._excluded_from_retrieval(element))
        retrievable_count = len(elements) - len(excluded_ids)
        emitted_element_ids = {element_id for unit in emitted for element_id in unit.element_ids}

        return RetrievalUnitBuildResult(
            document_id=document.document_id,
            strategy=self._document_strategy(document.structure.mode),
            policy=self._policy,
            source_element_count=len(elements),
            retrievable_element_count=retrievable_count,
            covered_element_count=len(emitted_element_ids),
            units=tuple(emitted),
            oversized_unit_ids=tuple(oversized),
            partitioned_logical_unit_ids=tuple(partitioned_logical_units),
            skipped_excluded_element_ids=excluded_ids,
            skipped_blank_element_ids=tuple(dict.fromkeys(skipped_blank)),
            unresolved_integrity_element_ids=tuple(unresolved_integrity),
        )

    def _from_logical_unit(
        self,
        document: CanonicalDocument,
        logical_unit: LogicalUnit,
        elements: tuple[Element, ...],
        nodes: dict[str, ContextNode],
        regions: dict[str, ContentRegion],
        subdocuments: tuple[SubDocument, ...],
    ) -> tuple[RetrievalUnit, ...]:
        # Validate the complete structural owner before partitioning. Otherwise a
        # malformed LogicalUnit that crosses source boundaries could be split into
        # individually valid fragments and silently hide the upstream error.
        self._resolve_subdocument(
            logical_unit.element_ids,
            subdocuments,
            owner=f"logical unit {logical_unit.id!r}",
        )
        self._unit_strategy(
            document,
            logical_unit.region_id,
            logical_unit.element_ids,
            regions,
        )

        context_path = self._context_refs(logical_unit.context_node_ids, nodes)
        partitions = self._partition_logical_elements(
            document,
            logical_unit,
            elements,
            context_path,
        )
        part_count = len(partitions)
        emitted: list[RetrievalUnit] = []
        for part_index, part_elements in enumerate(partitions):
            unit = self._make_logical_retrieval_unit(
                document,
                logical_unit,
                part_elements,
                context_path,
                regions,
                subdocuments,
                part_index=part_index,
                part_count=part_count,
            )
            if unit is not None:
                emitted.append(unit)
        return tuple(emitted)

    def _partition_logical_elements(
        self,
        document: CanonicalDocument,
        logical_unit: LogicalUnit,
        elements: tuple[Element, ...],
        context_path: tuple[ContextNodeRef, ...],
    ) -> tuple[tuple[Element, ...], ...]:
        if self._policy.max_tokens is None or not self._policy.adaptive_text_partitioning or logical_unit.type != LogicalUnitType.TEXT_BLOCK or len(elements) <= 1:
            return (elements,)

        partitions: list[tuple[Element, ...]] = []
        current: list[Element] = []

        for element in elements:
            candidate = (*current, element)
            content_text = self._join_retrieval_content(candidate)
            if not content_text.strip():
                current.append(element)
                continue

            retrieval_text = self._build_retrieval_text(
                document,
                content_text,
                context_path,
            )
            candidate_count = self._count_tokens(retrieval_text)
            current_content = self._join_retrieval_content(tuple(current))
            if current and current_content.strip() and self._budget_exceeded(candidate_count):
                partitions.append(tuple(current))
                current = [element]
            else:
                current.append(element)

        if current:
            partitions.append(tuple(current))
        return tuple(partitions) if partitions else (elements,)

    def _make_logical_retrieval_unit(
        self,
        document: CanonicalDocument,
        logical_unit: LogicalUnit,
        elements: tuple[Element, ...],
        context_path: tuple[ContextNodeRef, ...],
        regions: dict[str, ContentRegion],
        subdocuments: tuple[SubDocument, ...],
        *,
        part_index: int,
        part_count: int,
    ) -> RetrievalUnit | None:
        display_text, content_text, content_projection = self._logical_unit_views(
            logical_unit.type,
            elements,
        )
        if not display_text.strip() or not content_text.strip():
            return None

        element_ids = tuple(element.id for element in elements)
        retrieval_text = self._build_retrieval_text(
            document,
            content_text,
            context_path,
        )
        token_count = self._count_tokens(retrieval_text)
        subdocument_id = self._resolve_subdocument(
            element_ids,
            subdocuments,
            owner=f"logical unit {logical_unit.id!r}",
        )
        strategy = self._unit_strategy(
            document,
            logical_unit.region_id,
            element_ids,
            regions,
        )
        if part_count == 1:
            identity = f"logical:{logical_unit.id}"
        else:
            element_identity = ",".join(element_ids)
            identity = f"logical:{logical_unit.id}:part:{part_index + 1}/{part_count}:{element_identity}"
        unit_id = self._unit_id(
            document,
            identity=identity,
            retrieval_text=retrieval_text,
        )
        metadata = {
            "projection": "logical_unit",
            "content_projection": content_projection,
            "logical_unit_type": logical_unit.type.value,
            "strategy": strategy.value,
            "policy_version": self._policy.version,
            "semantic_enrichment_used": False,
            "location_projection": self._location_projection(elements),
            "token_budget_exceeded": self._budget_exceeded(token_count),
            "adaptive_partitioned": part_count > 1,
        }
        if part_count > 1:
            metadata.update(
                {
                    "partition_index": part_index,
                    "partition_count": part_count,
                    "partition_reason": "token_budget",
                    "source_logical_unit_id": logical_unit.id,
                }
            )
        if self._policy.max_tokens is not None:
            metadata["max_tokens"] = self._policy.max_tokens

        return RetrievalUnit(
            id=unit_id,
            document_id=document.document_id,
            content_hash=document.content_hash,
            source_revision=document.source_revision,
            subdocument_id=subdocument_id,
            logical_unit_ids=(logical_unit.id,),
            element_ids=element_ids,
            retrieval_text=retrieval_text,
            display_text=display_text,
            context_path=context_path,
            semantic_annotations=(),
            source_anchors=self._anchors(document, elements),
            unit_type=_LOGICAL_TO_RETRIEVAL_TYPE[logical_unit.type],
            token_count=token_count,
            quality=None,
            version=self.version,
            metadata=metadata,
        )

    def _from_single_element(
        self,
        document: CanonicalDocument,
        element: Element,
        nodes: dict[str, ContextNode],
        regions: dict[str, ContentRegion],
        subdocuments: tuple[SubDocument, ...],
    ) -> RetrievalUnit | None:
        display_text = self._display_text(element)
        content_text = self._retrieval_text(element)
        if display_text is None or content_text is None:
            return None

        context_ids = self._fallback_context_ids(element, nodes)
        context_path = self._context_refs(context_ids, nodes)
        retrieval_text = self._build_retrieval_text(
            document,
            content_text,
            context_path,
        )
        token_count = self._count_tokens(retrieval_text)
        subdocument_id = self._resolve_subdocument(
            (element.id,),
            subdocuments,
            owner=f"element {element.id!r}",
        )
        strategy = self._unit_strategy(
            document,
            None,
            (element.id,),
            regions,
        )
        unit_id = self._unit_id(
            document,
            identity=f"element:{element.id}",
            retrieval_text=retrieval_text,
        )
        metadata = {
            "projection": "fallback_element",
            "content_projection": "element",
            "element_type": element.type.value,
            "strategy": strategy.value,
            "policy_version": self._policy.version,
            "semantic_enrichment_used": False,
            "location_projection": self._location_projection((element,)),
            "token_budget_exceeded": self._budget_exceeded(token_count),
        }
        if self._policy.max_tokens is not None:
            metadata["max_tokens"] = self._policy.max_tokens

        return RetrievalUnit(
            id=unit_id,
            document_id=document.document_id,
            content_hash=document.content_hash,
            source_revision=document.source_revision,
            subdocument_id=subdocument_id,
            element_ids=(element.id,),
            retrieval_text=retrieval_text,
            display_text=display_text,
            context_path=context_path,
            semantic_annotations=(),
            source_anchors=self._anchors(document, (element,)),
            unit_type=_ELEMENT_TO_RETRIEVAL_TYPE.get(
                element.type,
                RetrievalUnitType.TEXT,
            ),
            token_count=token_count,
            quality=None,
            version=self.version,
            metadata=metadata,
        )

    def _build_retrieval_text(
        self,
        document: CanonicalDocument,
        content_text: str,
        context_path: tuple[ContextNodeRef, ...],
    ) -> str:
        parts: list[str] = []
        if self._policy.include_document_title:
            title = document.metadata.title
            if title is not None and title.strip():
                parts.append(title.strip())
        if self._policy.include_context_labels and context_path:
            labels = [ref.label for ref in context_path if ref.label is not None]
            if labels:
                parts.append(self._policy.context_separator.join(labels))
        parts.append(content_text)
        return self._policy.retrieval_separator.join(parts)

    def _logical_unit_views(
        self,
        logical_unit_type: LogicalUnitType,
        elements: tuple[Element, ...],
    ) -> tuple[str, str, str]:
        if logical_unit_type == LogicalUnitType.TABLE_BLOCK:
            display = self._table_view(elements, retrieval=False)
            retrieval = self._table_view(elements, retrieval=True)
            return display, retrieval, "table_cells"
        return (
            self._join_display_text(elements),
            self._join_retrieval_content(elements),
            "element_join",
        )

    def _table_view(
        self,
        elements: tuple[Element, ...],
        *,
        retrieval: bool,
    ) -> str:
        getter = self._retrieval_text if retrieval else self._display_text
        cells: list[tuple[int, int | None, int | None, str]] = []
        for source_index, element in enumerate(elements):
            if element.type != ElementType.TABLE_CELL:
                continue
            value = getter(element)
            if value is None:
                continue
            row_index = element.attributes.get("row_index")
            cell_index = element.attributes.get("cell_index")
            cells.append(
                (
                    source_index,
                    row_index if isinstance(row_index, int) and not isinstance(row_index, bool) else None,
                    cell_index if isinstance(cell_index, int) and not isinstance(cell_index, bool) else None,
                    value,
                )
            )

        if cells:
            positioned = all(row is not None and cell is not None for _, row, cell, _ in cells)
            if positioned:
                by_row: dict[int, list[tuple[int, str]]] = defaultdict(list)
                seen_positions: set[tuple[int, int]] = set()
                duplicate_position = False
                for _, row, cell, value in cells:
                    assert row is not None and cell is not None
                    position = (row, cell)
                    if position in seen_positions:
                        duplicate_position = True
                        break
                    seen_positions.add(position)
                    by_row[row].append((cell, value))
                if not duplicate_position:
                    return "\n".join("\t".join(value for _, value in sorted(by_row[row], key=lambda item: item[0])) for row in sorted(by_row))
            # Coordinates are optional source metadata. If they are absent or
            # inconsistent, preserve canonical source order without reusing the
            # aggregate TABLE_ROW text that would duplicate the cells.
            return "\n".join(value for _, _, _, value in cells)

        rows = [value for element in elements if element.type == ElementType.TABLE_ROW and (value := getter(element)) is not None]
        if rows:
            return "\n".join(rows)

        values = [value for element in elements if (value := getter(element))]
        return self._policy.display_separator.join(values)

    def _join_display_text(self, elements: tuple[Element, ...]) -> str:
        values = [value for element in elements if (value := self._display_text(element))]
        return self._policy.display_separator.join(values)

    def _join_retrieval_content(self, elements: tuple[Element, ...]) -> str:
        values = [value for element in elements if (value := self._retrieval_text(element))]
        return self._policy.display_separator.join(values)

    @staticmethod
    def _display_text(element: Element) -> str | None:
        for value in (element.raw_text, element.normalized_text):
            if value is not None and value.strip():
                return value
        return None

    @staticmethod
    def _retrieval_text(element: Element) -> str | None:
        for value in (element.normalized_text, element.raw_text):
            if value is not None and value.strip():
                return value
        return None

    def _excluded_from_retrieval(self, element: Element) -> bool:
        if element.exclude_from_retrieval:
            return True
        if self._policy.exclude_page_numbers and element.type == ElementType.PAGE_NUMBER:
            return True
        if not self._excluded_zones:
            return False
        try:
            zone = source_zone(element)
        except SourceAttributeError as exc:
            raise RetrievalUnitBuildError(f"cannot evaluate retrieval eligibility for element {element.id!r}: {exc}") from exc
        return zone is not None and zone.casefold() in self._excluded_zones

    def _count_tokens(self, text: str) -> int:
        count = self._token_counter(text)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise RetrievalUnitBuildError("token_counter must return a positive integer for non-blank retrieval text")
        return count

    def _is_oversized(self, unit: RetrievalUnit) -> bool:
        return self._budget_exceeded(unit.token_count)

    def _budget_exceeded(self, token_count: int) -> bool:
        return self._policy.max_tokens is not None and token_count > self._policy.max_tokens

    @staticmethod
    def _context_refs(
        context_ids: tuple[str, ...],
        nodes: dict[str, ContextNode],
    ) -> tuple[ContextNodeRef, ...]:
        refs: list[ContextNodeRef] = []
        for context_id in context_ids:
            node = nodes.get(context_id)
            if node is None:
                raise RetrievalUnitBuildError(f"retrieval projection references unknown context node {context_id!r}")
            refs.append(
                ContextNodeRef(
                    id=node.id,
                    type=node.type,
                    label=node.label,
                    source=node.source,
                    confidence=node.confidence,
                )
            )
        return tuple(refs)

    @classmethod
    def _fallback_context_ids(
        cls,
        element: Element,
        nodes: dict[str, ContextNode],
    ) -> tuple[str, ...]:
        anchored = [node for node in nodes.values() if node.attributes.get("anchor_element_id") == element.id]
        if not anchored:
            return ()
        if len(anchored) != 1:
            raise RetrievalUnitBuildError(f"element {element.id!r} anchors multiple context nodes")

        # For a heading/title singleton, its own display text is already present.
        # Prefix only the ancestor path to avoid duplicating the same heading in
        # retrieval_text while keeping canonical context.
        parent_id = anchored[0].parent_id
        if parent_id is None:
            return ()
        return cls._path_to_root(parent_id, nodes)

    @staticmethod
    def _path_to_root(
        node_id: str,
        nodes: dict[str, ContextNode],
    ) -> tuple[str, ...]:
        reversed_path: list[str] = []
        seen: set[str] = set()
        current: str | None = node_id
        while current is not None:
            if current in seen:
                raise RetrievalUnitBuildError(f"context hierarchy contains cycle at {current!r}")
            seen.add(current)
            node = nodes.get(current)
            if node is None:
                raise RetrievalUnitBuildError(f"context hierarchy references unknown node {current!r}")
            reversed_path.append(current)
            current = node.parent_id
        return tuple(reversed(reversed_path))

    @staticmethod
    def _anchors(
        document: CanonicalDocument,
        elements: tuple[Element, ...],
    ) -> tuple[SourceAnchor, ...]:
        anchors: list[SourceAnchor] = []
        for element in elements:
            location = element.location
            if location is not None and location.source is not None:
                anchors.append(
                    SourceAnchor(
                        source_id=document.document_id,
                        content_hash=document.content_hash,
                        source_revision=document.source_revision,
                        element_id=element.id,
                        location_source=location.source,
                        page=location.page,
                        bbox=location.bbox,
                        start_char=location.start_char,
                        end_char=location.end_char,
                        line_start=location.line_start,
                        line_end=location.line_end,
                    )
                )
            else:
                anchors.append(
                    SourceAnchor(
                        source_id=document.document_id,
                        content_hash=document.content_hash,
                        source_revision=document.source_revision,
                        element_id=element.id,
                    )
                )
        return tuple(anchors)

    @staticmethod
    def _location_projection(elements: tuple[Element, ...]) -> str:
        sourced = sum(element.location is not None and element.location.source is not None for element in elements)
        if sourced == 0:
            return "element_identity_only"
        if sourced == len(elements):
            return "canonical_source_location"
        return "partial_canonical_source_location"

    @staticmethod
    def _resolve_subdocument(
        element_ids: tuple[str, ...],
        subdocuments: tuple[SubDocument, ...],
        *,
        owner: str,
    ) -> str | None:
        member_set = set(element_ids)
        intersecting = [subdocument for subdocument in subdocuments if member_set.intersection(subdocument.element_ids)]
        if not intersecting:
            return None
        containing = [subdocument for subdocument in intersecting if member_set.issubset(subdocument.element_ids)]
        if len(intersecting) != 1 or len(containing) != 1:
            raise RetrievalUnitBuildError(f"{owner} crosses a SubDocument boundary; retrieval units must not merge across source-document boundaries")
        return containing[0].id

    @staticmethod
    def _validate_logical_unit_ownership(
        logical_units: tuple[LogicalUnit, ...],
    ) -> None:
        owners: dict[str, str] = {}
        for unit in logical_units:
            for element_id in unit.element_ids:
                previous = owners.get(element_id)
                if previous is not None:
                    raise RetrievalUnitBuildError(
                        f"element {element_id!r} belongs to multiple logical units ({previous!r}, {unit.id!r}); retrieval projection requires one structural owner per element"
                    )
                owners[element_id] = unit.id

    @staticmethod
    def _document_strategy(mode: StructureMode) -> RetrievalStrategy:
        return {
            StructureMode.UNKNOWN: RetrievalStrategy.FLAT,
            StructureMode.FLAT: RetrievalStrategy.FLAT,
            StructureMode.LOCAL: RetrievalStrategy.LOCAL,
            StructureMode.GROUPED: RetrievalStrategy.GROUPED,
            StructureMode.HIERARCHICAL: RetrievalStrategy.HIERARCHICAL,
            StructureMode.MIXED: RetrievalStrategy.MIXED,
        }[mode]

    @classmethod
    def _unit_strategy(
        cls,
        document: CanonicalDocument,
        declared_region_id: str | None,
        element_ids: tuple[str, ...],
        regions: dict[str, ContentRegion],
    ) -> RetrievalStrategy:
        member_set = set(element_ids)
        global_strategy = cls._document_strategy(document.structure.mode)

        if declared_region_id is not None:
            declared = regions.get(declared_region_id)
            if declared is None:
                raise RetrievalUnitBuildError(f"retrieval projection references unknown region {declared_region_id!r}")
            if not member_set.issubset(declared.element_ids):
                raise RetrievalUnitBuildError(f"retrieval projection contains elements outside declared region {declared_region_id!r}")
            if declared.structure.mode != StructureMode.UNKNOWN:
                return cls._document_strategy(declared.structure.mode)
            return RetrievalStrategy.FLAT if document.structure.mode == StructureMode.MIXED else global_strategy

        intersecting = [region for region in regions.values() if member_set.intersection(region.element_ids)]
        if intersecting:
            containing = [region for region in intersecting if member_set.issubset(region.element_ids)]
            if len(intersecting) != 1 or len(containing) != 1:
                raise RetrievalUnitBuildError("retrieval projection crosses ContentRegion boundaries")
            region = containing[0]
            if region.structure.mode != StructureMode.UNKNOWN:
                return cls._document_strategy(region.structure.mode)
            return RetrievalStrategy.FLAT if document.structure.mode == StructureMode.MIXED else global_strategy

        if document.structure.mode == StructureMode.MIXED:
            return RetrievalStrategy.FLAT
        return global_strategy

    def _unit_id(
        self,
        document: CanonicalDocument,
        *,
        identity: str,
        retrieval_text: str,
    ) -> str:
        value = "|".join(
            (
                self.version,
                self._policy.model_dump_json(),
                document.document_id,
                document.content_hash,
                document.source_revision or "",
                identity,
                retrieval_text,
            )
        )
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
        return f"ru_{digest}"
