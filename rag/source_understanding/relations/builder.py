from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import (
    Confidence,
    Identifier,
    JsonObject,
    SchemaModel,
    StructureSource,
)
from rag.source_understanding.schemas.document import SubDocument
from rag.source_understanding.schemas.element import Element, ElementType
from rag.source_understanding.schemas.logical_unit import LogicalUnit, LogicalUnitType
from rag.source_understanding.schemas.relation import Relation, RelationLayer, RelationType
from rag.source_understanding.source_attributes import (
    INTEGRITY_GROUP_ID_ATTRIBUTE,
    INTEGRITY_PARENT_GROUP_ID_ATTRIBUTE,
    SourceAttributeError,
    source_anchor,
    source_references,
)

from .table_continuation import (
    TABLE_CONTINUATION_CONTRACT_VERSION,
    TABLE_CONTINUATION_EVIDENCE_ATTRIBUTE,
    TABLE_CONTINUATION_EVIDENCE_COMPARISON_TOLERANCE,
    TableContinuationEvidence,
)

RELATION_BUILDER_VERSION = "3"


class RelationBuildError(ValueError):
    """Structural relations cannot be built from inconsistent canonical objects."""


class RelationBuildPolicy(SchemaModel):
    version: str = RELATION_BUILDER_VERSION
    include_element_next: bool = True
    include_element_membership: bool = True
    include_question_answer: bool = True
    include_subdocument_membership: bool = True
    include_integrity_nesting: bool = True
    include_explicit_source_references: bool = True
    enable_table_continuation: bool = True
    table_continuation_version: str = TABLE_CONTINUATION_CONTRACT_VERSION
    table_continuation_max_edge_distance: float = Field(default=0.12, ge=0.0, le=0.5)
    table_continuation_max_width_delta: float = Field(default=0.08, ge=0.0, le=1.0)
    table_continuation_max_column_boundary_delta: float = Field(
        default=0.04,
        ge=0.0,
        le=1.0,
    )
    table_continuation_min_evidence_signals: int = Field(default=4, ge=2, le=8)
    table_continuation_confidence: Confidence = 0.92
    table_continuation_evidence_comparison_tolerance: float = Field(
        default=TABLE_CONTINUATION_EVIDENCE_COMPARISON_TOLERANCE,
        ge=0.0,
        le=0.001,
    )
    deterministic_confidence: Confidence = 1.0


class RelationDiagnosticOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    INSPECTION_FAILED = "INSPECTION_FAILED"
    AMBIGUOUS = "AMBIGUOUS"


class RelationBuildDiagnostic(SchemaModel):
    code: str = Field(min_length=1, max_length=128)
    outcome: RelationDiagnosticOutcome
    reason: str = Field(min_length=1, max_length=256)
    source_id: Identifier | None = None
    target_id: Identifier | None = None
    metadata: JsonObject = Field(default_factory=dict)


class _NoContinuationEvidence(ValueError):
    """A table belongs to another source adapter or predates M2.7 evidence."""


@dataclass(frozen=True, slots=True)
class _TableFragment:
    unit: LogicalUnit
    evidence: TableContinuationEvidence
    anchor: tuple[str, str]


class RelationBuildResult(SchemaModel):
    version: str = RELATION_BUILDER_VERSION
    element_count: int = Field(ge=1)
    logical_unit_count: int = Field(ge=0)
    subdocument_count: int = Field(ge=0)
    policy: RelationBuildPolicy
    relations: tuple[Relation, ...] = Field(default_factory=tuple)
    diagnostics: tuple[RelationBuildDiagnostic, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_unique_relations(self) -> RelationBuildResult:
        ids = [relation.id for relation in self.relations]
        if len(ids) != len(set(ids)):
            raise ValueError("relation builder produced duplicate relation ids")
        triples = [(relation.type, relation.source_id, relation.target_id) for relation in self.relations]
        if len(triples) != len(set(triples)):
            raise ValueError("relation builder produced duplicate relation triples")
        return self


class StructuralRelationBuilder:
    """Build only structural relations directly supported by canonical structure."""

    version: str = RELATION_BUILDER_VERSION

    def __init__(self, policy: RelationBuildPolicy | None = None) -> None:
        self._policy = policy if policy is not None else RelationBuildPolicy()

    def build(
        self,
        elements: Sequence[Element],
        logical_units: Sequence[LogicalUnit],
        subdocuments: Sequence[SubDocument] = (),
    ) -> RelationBuildResult:
        element_snapshot = tuple(elements)
        unit_snapshot = tuple(logical_units)
        subdoc_snapshot = tuple(subdocuments)
        self._validate_inputs(element_snapshot, unit_snapshot, subdoc_snapshot)
        relations: list[Relation] = []
        diagnostics: list[RelationBuildDiagnostic] = []

        if self._policy.include_element_next:
            for left, right in itertools.pairwise(element_snapshot):
                relations.append(
                    self._make_relation(
                        RelationType.NEXT,
                        left.id,
                        right.id,
                        source=StructureSource.DERIVED,
                        confidence=self._policy.deterministic_confidence,
                        metadata={
                            "basis": "canonical_element_order",
                            "from_order": left.order,
                            "to_order": right.order,
                        },
                    )
                )

        if self._policy.include_element_membership:
            for unit in unit_snapshot:
                for element_id in unit.element_ids:
                    relations.append(
                        self._make_relation(
                            RelationType.PART_OF,
                            element_id,
                            unit.id,
                            source=StructureSource.DERIVED,
                            confidence=self._policy.deterministic_confidence,
                            metadata={
                                "membership": "logical_unit",
                                "logical_unit_type": unit.type.value,
                            },
                        )
                    )

        if self._policy.include_integrity_nesting:
            by_native_group: dict[str, LogicalUnit] = {}
            for unit in unit_snapshot:
                group_id = unit.metadata.get(INTEGRITY_GROUP_ID_ATTRIBUTE)
                if group_id is None:
                    continue
                if not isinstance(group_id, str) or not group_id:
                    raise RelationBuildError(f"logical unit {unit.id!r} has invalid native integrity group id")
                previous = by_native_group.get(group_id)
                if previous is not None and previous.id != unit.id:
                    raise RelationBuildError(f"native integrity group {group_id!r} maps to multiple logical units")
                by_native_group[group_id] = unit
            for unit in unit_snapshot:
                parent_group = unit.metadata.get(INTEGRITY_PARENT_GROUP_ID_ATTRIBUTE)
                if parent_group is None:
                    continue
                if not isinstance(parent_group, str) or not parent_group:
                    raise RelationBuildError(f"logical unit {unit.id!r} has invalid native integrity parent id")
                parent = by_native_group.get(parent_group)
                if parent is None:
                    raise RelationBuildError(f"logical unit {unit.id!r} references missing native integrity parent group {parent_group!r}")
                relations.append(
                    self._make_relation(
                        RelationType.PART_OF,
                        unit.id,
                        parent.id,
                        source=StructureSource.DERIVED,
                        confidence=min(unit.confidence, parent.confidence),
                        metadata={
                            "membership": "native_integrity_parent",
                            "child_group_id": unit.metadata.get(INTEGRITY_GROUP_ID_ATTRIBUTE),
                            "parent_group_id": parent_group,
                        },
                    )
                )

        if self._policy.include_explicit_source_references:
            anchors: dict[tuple[str, str], str] = {}
            try:
                for element in element_snapshot:
                    anchor = source_anchor(element)
                    if anchor is None:
                        continue
                    previous = anchors.get(anchor)
                    if previous is not None and previous != element.id:
                        raise RelationBuildError(f"source anchor {anchor!r} maps to multiple elements")
                    anchors[anchor] = element.id

                for referring in element_snapshot:
                    seen_refs: set[tuple[str, str]] = set()
                    for kind, reference_id in source_references(referring):
                        key = (kind, reference_id)
                        if key in seen_refs:
                            continue
                        seen_refs.add(key)
                        anchored_id = anchors.get(key)
                        if anchored_id is None:
                            continue
                        if kind in {"footnote", "endnote"}:
                            relations.append(
                                self._make_relation(
                                    RelationType.FOOTNOTE_OF,
                                    anchored_id,
                                    referring.id,
                                    source=StructureSource.EXPLICIT,
                                    confidence=self._policy.deterministic_confidence,
                                    metadata={
                                        "basis": "explicit_source_reference",
                                        "reference_kind": kind,
                                        "reference_id": reference_id,
                                    },
                                )
                            )
            except SourceAttributeError as exc:
                raise RelationBuildError(str(exc)) from exc

        if self._policy.include_question_answer:
            for unit in unit_snapshot:
                if unit.type != LogicalUnitType.QA_PAIR:
                    continue
                if len(unit.element_ids) != 2:
                    raise RelationBuildError(f"QA_PAIR logical unit {unit.id!r} must contain exactly two elements")
                question_id, answer_id = unit.element_ids
                relations.append(
                    self._make_relation(
                        RelationType.QUESTION_ANSWER,
                        question_id,
                        answer_id,
                        source=unit.source,
                        confidence=unit.confidence,
                        metadata={"logical_unit_id": unit.id},
                    )
                )

        if self._policy.include_subdocument_membership and subdoc_snapshot:
            for unit in unit_snapshot:
                containing = [subdoc for subdoc in subdoc_snapshot if set(unit.element_ids).issubset(subdoc.element_ids)]
                intersecting = [subdoc for subdoc in subdoc_snapshot if set(unit.element_ids).intersection(subdoc.element_ids)]
                if intersecting and len(containing) != 1:
                    raise RelationBuildError(f"logical unit {unit.id!r} crosses or ambiguously intersects subdocument boundaries")
                if not containing:
                    continue
                subdoc = containing[0]
                relations.append(
                    self._make_relation(
                        RelationType.PART_OF,
                        unit.id,
                        subdoc.id,
                        source=StructureSource.DERIVED,
                        confidence=min(unit.confidence, subdoc.confidence),
                        metadata={"membership": "subdocument"},
                    )
                )

        if self._policy.enable_table_continuation:
            relations.extend(
                self._build_table_continuations(
                    element_snapshot,
                    unit_snapshot,
                    diagnostics,
                )
            )

        return RelationBuildResult(
            element_count=len(element_snapshot),
            logical_unit_count=len(unit_snapshot),
            subdocument_count=len(subdoc_snapshot),
            policy=self._policy,
            relations=tuple(relations),
            diagnostics=tuple(diagnostics),
        )

    def _build_table_continuations(
        self,
        elements: tuple[Element, ...],
        logical_units: tuple[LogicalUnit, ...],
        diagnostics: list[RelationBuildDiagnostic],
    ) -> list[Relation]:
        fragments_by_page: dict[int, list[_TableFragment]] = {}
        for unit in logical_units:
            table_elements = [element for element in elements if element.id in unit.element_ids and element.type == ElementType.TABLE]
            if not table_elements:
                continue
            try:
                fragment = self._table_fragment(unit, table_elements)
            except _NoContinuationEvidence:
                continue
            except Exception as exc:  # noqa: BLE001 - one relation strategy failure becomes a diagnostic.
                diagnostics.append(
                    RelationBuildDiagnostic(
                        code="TABLE_CONTINUATION_INSPECTION_FAILED",
                        outcome=RelationDiagnosticOutcome.INSPECTION_FAILED,
                        reason="source_ownership_invalid",
                        source_id=unit.id,
                        metadata={
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "contract_version": self._policy.table_continuation_version,
                        },
                    )
                )
                continue
            fragments_by_page.setdefault(fragment.evidence.page, []).append(fragment)

        relations: list[Relation] = []
        for page in sorted(fragments_by_page):
            next_page = fragments_by_page.get(page + 1)
            if not next_page:
                continue
            current_page = tuple(sorted(fragments_by_page[page], key=lambda item: item.unit.id))
            following_page = tuple(sorted(next_page, key=lambda item: item.unit.id))
            accepted: list[tuple[_TableFragment, _TableFragment, tuple[str, ...]]] = []
            for left in current_page:
                for right in following_page:
                    passed, reason, signals = self._evaluate_table_pair(left, right)
                    if passed:
                        accepted.append((left, right, signals))
                    else:
                        diagnostics.append(
                            RelationBuildDiagnostic(
                                code="TABLE_CONTINUATION_CANDIDATE_REJECTED",
                                outcome=RelationDiagnosticOutcome.REJECTED,
                                reason=reason,
                                source_id=left.unit.id,
                                target_id=right.unit.id,
                                metadata={
                                    "page_pair": [page, page + 1],
                                    "contract_version": self._policy.table_continuation_version,
                                },
                            )
                        )
            if len(accepted) != 1:
                if len(accepted) > 1:
                    diagnostics.append(
                        RelationBuildDiagnostic(
                            code="TABLE_CONTINUATION_AMBIGUOUS",
                            outcome=RelationDiagnosticOutcome.AMBIGUOUS,
                            reason="multiple_candidate_pairs",
                            metadata={
                                "page_pair": [page, page + 1],
                                "candidate_pairs": [[left.unit.id, right.unit.id] for left, right, _signals in accepted],
                                "contract_version": self._policy.table_continuation_version,
                            },
                        )
                    )
                continue

            left, right, signals = accepted[0]
            relation = self._make_relation(
                RelationType.CONTINUES,
                left.unit.id,
                right.unit.id,
                source=StructureSource.INFERRED,
                confidence=min(
                    self._policy.table_continuation_confidence,
                    left.unit.confidence,
                    right.unit.confidence,
                ),
                metadata={
                    "basis": "adjacent_page_table_geometry",
                    "contract_version": self._policy.table_continuation_version,
                    "page_pair": [page, page + 1],
                    "evidence_signals": list(signals),
                    "source_table_anchor": {
                        "kind": left.anchor[0],
                        "id": left.anchor[1],
                    },
                    "target_table_anchor": {
                        "kind": right.anchor[0],
                        "id": right.anchor[1],
                    },
                },
            )
            relations.append(relation)
            diagnostics.append(
                RelationBuildDiagnostic(
                    code="TABLE_CONTINUATION_ACCEPTED",
                    outcome=RelationDiagnosticOutcome.ACCEPTED,
                    reason="compatible_adjacent_table_fragments",
                    source_id=left.unit.id,
                    target_id=right.unit.id,
                    metadata={
                        "page_pair": [page, page + 1],
                        "evidence_signals": list(signals),
                        "contract_version": self._policy.table_continuation_version,
                    },
                )
            )
        return relations

    def _table_fragment(
        self,
        unit: LogicalUnit,
        table_elements: list[Element],
    ) -> _TableFragment:
        if len(table_elements) != 1:
            raise RelationBuildError(f"logical unit {unit.id!r} must contain exactly one TABLE element")
        table = table_elements[0]
        evidence_value = table.attributes.get(TABLE_CONTINUATION_EVIDENCE_ATTRIBUTE)
        if evidence_value is None:
            raise _NoContinuationEvidence
        evidence = TableContinuationEvidence.model_validate(evidence_value)
        if evidence.version != self._policy.table_continuation_version:
            raise RelationBuildError(f"table continuation evidence version {evidence.version!r} does not match policy {self._policy.table_continuation_version!r}")
        if table.location is None or table.location.page is None:
            raise RelationBuildError("table continuation table element has no page location")
        if table.location.page != evidence.page:
            raise RelationBuildError("table continuation evidence page disagrees with element")
        if table.location.bbox is None:
            raise RelationBuildError("table continuation table element has no bbox")
        location_bbox = (
            table.location.bbox.x0,
            table.location.bbox.y0,
            table.location.bbox.x1,
            table.location.bbox.y1,
        )
        if any(abs(left - right) > self._policy.table_continuation_evidence_comparison_tolerance for left, right in zip(location_bbox, evidence.bbox)):
            raise RelationBuildError("table continuation evidence bbox disagrees with element")
        for attribute, expected in (
            ("row_count", evidence.row_count),
            ("column_count", evidence.column_count),
        ):
            observed = table.attributes.get(attribute)
            if observed is not None and observed != expected:
                raise RelationBuildError(f"table continuation evidence {attribute} disagrees with element")
        anchor = source_anchor(table)
        if anchor is None:
            raise RelationBuildError("table continuation table element has no source anchor")
        return _TableFragment(unit=unit, evidence=evidence, anchor=anchor)

    def _evaluate_table_pair(
        self,
        left: _TableFragment,
        right: _TableFragment,
    ) -> tuple[bool, str, tuple[str, ...]]:
        if right.evidence.page != left.evidence.page + 1:
            return False, "non_adjacent_pages", ()
        signals = ["adjacent_pages"]
        left_bottom_distance = 1.0 - left.evidence.bbox[3]
        right_top_distance = right.evidence.bbox[1]
        if left_bottom_distance > self._policy.table_continuation_max_edge_distance or right_top_distance > self._policy.table_continuation_max_edge_distance:
            return False, "page_edge_evidence_insufficient", ()
        signals.append("page_edge_proximity")
        if left.evidence.column_count != right.evidence.column_count:
            return False, "column_count_mismatch", ()
        signals.append("column_count")
        if len(left.evidence.column_boundaries) != len(right.evidence.column_boundaries):
            return False, "column_geometry_mismatch", ()
        max_lane_delta = max(abs(a - b) for a, b in zip(left.evidence.column_boundaries, right.evidence.column_boundaries))
        if max_lane_delta > self._policy.table_continuation_max_column_boundary_delta:
            return False, "column_geometry_mismatch", ()
        signals.append("column_geometry")
        left_width = left.evidence.bbox[2] - left.evidence.bbox[0]
        right_width = right.evidence.bbox[2] - right.evidence.bbox[0]
        if abs(left_width - right_width) > self._policy.table_continuation_max_width_delta:
            return False, "table_width_mismatch", ()
        signals.append("table_width")
        if left.evidence.topology != right.evidence.topology:
            return False, "table_topology_incompatible", ()
        signals.append("table_topology")
        if left.evidence.orientation is not None and right.evidence.orientation is not None and left.evidence.orientation != right.evidence.orientation:
            return False, "page_orientation_mismatch", ()
        if left.evidence.orientation is not None and right.evidence.orientation is not None:
            signals.append("page_orientation")
        if left.evidence.leading_row_fingerprint is not None and left.evidence.leading_row_fingerprint == right.evidence.leading_row_fingerprint:
            signals.append("leading_row_match")
        if len(signals) < self._policy.table_continuation_min_evidence_signals:
            return False, "evidence_insufficient", tuple(signals)
        return True, "compatible_adjacent_table_fragments", tuple(signals)

    @staticmethod
    def _relation_id(
        relation_type: RelationType,
        source_id: str,
        target_id: str,
    ) -> str:
        identity = f"{relation_type.value}|{source_id}|{target_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return f"rel_{digest}"

    def _make_relation(
        self,
        relation_type: RelationType,
        source_id: str,
        target_id: str,
        *,
        source: StructureSource,
        confidence: float,
        metadata: dict[str, object],
    ) -> Relation:
        return Relation(
            id=self._relation_id(relation_type, source_id, target_id),
            layer=RelationLayer.STRUCTURAL,
            type=relation_type,
            source_id=source_id,
            target_id=target_id,
            confidence=confidence,
            source=source,
            metadata=metadata,
        )

    @staticmethod
    def _validate_inputs(
        elements: tuple[Element, ...],
        logical_units: tuple[LogicalUnit, ...],
        subdocuments: tuple[SubDocument, ...],
    ) -> None:
        if not elements:
            raise RelationBuildError("cannot build structural relations for an empty source")
        element_ids = [element.id for element in elements]
        if len(element_ids) != len(set(element_ids)):
            raise RelationBuildError("elements must have unique ids")
        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise RelationBuildError("elements must have unique order values")
        if orders != sorted(orders):
            raise RelationBuildError("elements must follow canonical source order")
        unit_ids = [unit.id for unit in logical_units]
        subdoc_ids = [subdoc.id for subdoc in subdocuments]
        namespaces = (set(element_ids), set(unit_ids), set(subdoc_ids))
        if len(unit_ids) != len(namespaces[1]):
            raise RelationBuildError("logical units must have unique ids")
        if len(subdoc_ids) != len(namespaces[2]):
            raise RelationBuildError("subdocuments must have unique ids")
        if namespaces[0] & namespaces[1] or namespaces[0] & namespaces[2] or namespaces[1] & namespaces[2]:
            raise RelationBuildError("element/logical-unit/subdocument ids must not collide")
        order_by_id = {element.id: index for index, element in enumerate(elements)}
        valid_elements = set(element_ids)
        for unit in logical_units:
            missing = set(unit.element_ids) - valid_elements
            if missing:
                raise RelationBuildError(f"logical unit {unit.id!r} references unknown elements: {sorted(missing)}")
            positions = [order_by_id[element_id] for element_id in unit.element_ids]
            if positions != sorted(positions):
                raise RelationBuildError(f"logical unit {unit.id!r} does not follow canonical element order")
        seen_subdoc_elements: set[str] = set()
        for subdoc in subdocuments:
            missing = set(subdoc.element_ids) - valid_elements
            if missing:
                raise RelationBuildError(f"subdocument {subdoc.id!r} references unknown elements: {sorted(missing)}")
            positions = [order_by_id[element_id] for element_id in subdoc.element_ids]
            if positions != sorted(positions):
                raise RelationBuildError(f"subdocument {subdoc.id!r} does not follow canonical element order")
            overlap = seen_subdoc_elements.intersection(subdoc.element_ids)
            if overlap:
                raise RelationBuildError(f"subdocuments overlap on elements: {sorted(overlap)}")
            seen_subdoc_elements.update(subdoc.element_ids)
