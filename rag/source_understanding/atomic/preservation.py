from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import SchemaModel
from rag.source_understanding.schemas.element import Element, RawElement

PARSER_PRESERVATION_EVALUATOR_VERSION = "1"


class ParserPreservationReport(SchemaModel):
    """Loss-oriented audit between adapter observations and canonical Elements."""

    version: str = PARSER_PRESERVATION_EVALUATOR_VERSION
    raw_element_count: int = Field(ge=0)
    canonical_element_count: int = Field(ge=0)
    aligned_element_count: int = Field(ge=0)
    exact_element_count: int = Field(ge=0)
    type_hint_preserved_count: int = Field(ge=0)
    raw_text_exact_count: int = Field(ge=0)
    attributes_exact_count: int = Field(ge=0)
    style_exact_count: int = Field(ge=0)
    location_exact_count: int = Field(ge=0)
    provenance_preserved_count: int = Field(ge=0)
    cardinality_preserved: bool
    order_preserved: bool
    type_hint_preservation_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    raw_text_preservation_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    exact_element_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    fully_preserved: bool
    issues: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_counts(self) -> ParserPreservationReport:
        for name in (
            "aligned_element_count",
            "exact_element_count",
            "type_hint_preserved_count",
            "raw_text_exact_count",
            "attributes_exact_count",
            "style_exact_count",
            "location_exact_count",
            "provenance_preserved_count",
        ):
            if getattr(self, name) > self.raw_element_count:
                raise ValueError(f"{name} cannot exceed raw_element_count")
        expected_full = self.cardinality_preserved and self.order_preserved and self.exact_element_count == self.raw_element_count and not self.issues
        if self.fully_preserved != expected_full:
            raise ValueError("fully_preserved disagrees with preservation evidence")
        return self


def evaluate_parser_preservation(
    raw_elements: Sequence[RawElement],
    elements: Sequence[Element],
) -> ParserPreservationReport:
    """Compare source-near observations without accepting inferred replacements.

    Alignment is by explicit source order. The audit deliberately does not compare
    ``normalized_text`` or canonical type because those are transformations rather
    than preserved source facts.
    """

    raw_snapshot = tuple(raw_elements)
    element_snapshot = tuple(elements)
    raw_orders = tuple(item.order for item in raw_snapshot)
    canonical_orders = tuple(item.order for item in element_snapshot)
    cardinality_preserved = len(raw_snapshot) == len(element_snapshot)
    order_preserved = raw_orders == canonical_orders
    issues: list[str] = []

    if not cardinality_preserved:
        issues.append(f"element cardinality changed: raw={len(raw_snapshot)}, canonical={len(element_snapshot)}")
    if not order_preserved:
        issues.append(f"canonical order differs from adapter order: raw={raw_orders}, canonical={canonical_orders}")

    canonical_by_order: dict[int, Element] = {}
    duplicate_canonical_orders: set[int] = set()
    for element in element_snapshot:
        if element.order in canonical_by_order:
            duplicate_canonical_orders.add(element.order)
        canonical_by_order[element.order] = element
    if duplicate_canonical_orders:
        issues.append(f"canonical elements contain duplicate orders: {sorted(duplicate_canonical_orders)}")

    aligned = 0
    exact = 0
    type_hint_preserved = 0
    raw_text_exact = 0
    attributes_exact = 0
    style_exact = 0
    location_exact = 0
    provenance_preserved = 0

    for raw in raw_snapshot:
        element = canonical_by_order.get(raw.order)
        if element is None:
            issues.append(f"raw element at order {raw.order} has no canonical element")
            continue
        aligned += 1
        type_hint_matches = element.source_type_hint == raw.type_hint
        text_matches = element.raw_text == raw.text
        attributes_match = element.attributes == raw.attributes
        style_matches = element.style == raw.style
        location_matches = element.location == raw.location
        provenance_matches = _provenance_preserved(raw, element)

        type_hint_preserved += int(type_hint_matches)
        raw_text_exact += int(text_matches)
        attributes_exact += int(attributes_match)
        style_exact += int(style_matches)
        location_exact += int(location_matches)
        provenance_preserved += int(provenance_matches)
        element_exact = all(
            (
                text_matches,
                type_hint_matches,
                attributes_match,
                style_matches,
                location_matches,
                provenance_matches,
            )
        )
        exact += int(element_exact)
        if not element_exact:
            changed = [
                name
                for name, matched in (
                    ("source_type_hint", type_hint_matches),
                    ("raw_text", text_matches),
                    ("attributes", attributes_match),
                    ("style", style_matches),
                    ("location", location_matches),
                    ("provenance", provenance_matches),
                )
                if not matched
            ]
            issues.append(f"source facts changed at order {raw.order}: {', '.join(changed)}")

    denominator = len(raw_snapshot)
    fully_preserved = cardinality_preserved and order_preserved and exact == denominator and not issues
    return ParserPreservationReport(
        raw_element_count=denominator,
        canonical_element_count=len(element_snapshot),
        aligned_element_count=aligned,
        exact_element_count=exact,
        type_hint_preserved_count=type_hint_preserved,
        raw_text_exact_count=raw_text_exact,
        attributes_exact_count=attributes_exact,
        style_exact_count=style_exact,
        location_exact_count=location_exact,
        provenance_preserved_count=provenance_preserved,
        cardinality_preserved=cardinality_preserved,
        order_preserved=order_preserved,
        type_hint_preservation_ratio=(type_hint_preserved / denominator if denominator else None),
        raw_text_preservation_ratio=(raw_text_exact / denominator if denominator else None),
        exact_element_ratio=(exact / denominator if denominator else None),
        fully_preserved=fully_preserved,
        issues=tuple(issues),
    )


def _provenance_preserved(raw: RawElement, element: Element) -> bool:
    source = raw.provenance
    canonical = element.provenance
    source_transformations = source.transformations
    return (
        canonical.source == source.source
        and canonical.extractor == source.extractor
        and canonical.extractor_version == source.extractor_version
        and canonical.confidence == source.confidence
        and canonical.metadata == source.metadata
        and canonical.transformations[: len(source_transformations)] == source_transformations
    )
