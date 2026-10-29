from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from itertools import pairwise

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import Confidence, SchemaModel
from rag.source_understanding.schemas.document import DocumentQuality
from rag.source_understanding.schemas.element import BoundingBox, Element, ElementType
from rag.source_understanding.source_attributes import SourceAttributeError, source_zone

ORDER_QUALITY_ESTIMATOR_VERSION = "1"


class OrderQualityError(ValueError):
    pass


class OrderQualityPolicy(SchemaModel):
    version: str = ORDER_QUALITY_ESTIMATOR_VERSION
    page_regression_weight: Confidence = 0.50
    bbox_inversion_weight: Confidence = 0.35
    zone_interruption_weight: Confidence = 0.15
    horizontal_overlap_threshold: Confidence = 0.50
    vertical_tolerance: Confidence = 0.01
    low_quality_warning_threshold: Confidence = 0.90
    low_assessment_coverage_warning_threshold: Confidence = 0.80
    unassessed_signal_penalty: Confidence = 0.50

    @model_validator(mode="after")
    def validate_weights(self) -> OrderQualityPolicy:
        total = self.page_regression_weight + self.bbox_inversion_weight + self.zone_interruption_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError("order quality component weights must sum to 1.0")
        return self


class OrderQualityMetrics(SchemaModel):
    element_count: int = Field(ge=1)
    page_located_element_count: int = Field(ge=0)
    bbox_located_element_count: int = Field(ge=0)
    page_coverage_ratio: Confidence
    bbox_coverage_ratio: Confidence
    comparable_page_pair_count: int = Field(ge=0)
    page_regression_count: int = Field(ge=0)
    page_regression_ratio: Confidence
    comparable_bbox_pair_count: int = Field(ge=0)
    bbox_inversion_count: int = Field(ge=0)
    bbox_inversion_ratio: Confidence
    zone_transition_count: int = Field(ge=0)
    zone_interruption_count: int = Field(ge=0)
    zone_interruption_ratio: Confidence
    assessed: bool
    assessment_coverage: Confidence


class OrderQualityReport(SchemaModel):
    version: str = ORDER_QUALITY_ESTIMATOR_VERSION
    policy: OrderQualityPolicy
    metrics: OrderQualityMetrics
    quality: DocumentQuality


class OrderQualityEstimator:
    """Assess observed reading order without inferring or rewriting it."""

    version = ORDER_QUALITY_ESTIMATOR_VERSION

    def __init__(self, policy: OrderQualityPolicy | None = None) -> None:
        self._policy = policy if policy is not None else OrderQualityPolicy()

    def estimate(self, elements: Sequence[Element]) -> OrderQualityReport:
        snapshot = tuple(elements)
        if not snapshot:
            raise OrderQualityError("cannot estimate order quality for an empty source")

        page_located = tuple(element for element in snapshot if element.location is not None and element.location.page is not None)
        bbox_located = tuple(element for element in page_located if element.location is not None and element.location.bbox is not None)
        page_pairs = tuple(
            (left, right)
            for left, right in pairwise(page_located)
        )
        page_regressions = sum(
            right.location.page < left.location.page
            for left, right in page_pairs
            if left.location is not None and right.location is not None
        )

        bbox_pairs: list[tuple[BoundingBox, BoundingBox]] = []
        by_page: dict[int, list[Element]] = defaultdict(list)
        for element in bbox_located:
            if element.location is not None and element.location.page is not None:
                by_page[element.location.page].append(element)
        for page_elements in by_page.values():
            for left, right in pairwise(page_elements):
                if left.location is None or right.location is None:
                    continue
                left_box = left.location.bbox
                right_box = right.location.bbox
                if left_box is not None and right_box is not None and self._horizontally_comparable(left_box, right_box):
                    bbox_pairs.append((left_box, right_box))
        bbox_inversions = sum(right.y0 + self._policy.vertical_tolerance < left.y0 for left, right in bbox_pairs)

        zone_transitions = 0
        zone_interruptions = 0
        previous_page: int | None = None
        previous_zone: str | None = None
        for element in snapshot:
            page = element.location.page if element.location is not None else None
            zone = self._zone(element)
            if page != previous_page:
                previous_zone = None
            if previous_zone is not None and zone != previous_zone:
                zone_transitions += 1
                if (previous_zone == "footer" and zone == "body") or (previous_zone == "body" and zone == "header"):
                    zone_interruptions += 1
            previous_page = page
            previous_zone = zone

        page_regression_ratio = page_regressions / max(1, len(page_pairs))
        bbox_inversion_ratio = bbox_inversions / max(1, len(bbox_pairs))
        zone_interruption_ratio = zone_interruptions / max(1, zone_transitions)
        assessed = bool(page_pairs or bbox_pairs or zone_transitions)
        assessment_coverage = min(
            1.0,
            (self._policy.page_regression_weight if page_pairs else 0.0)
            + self._policy.bbox_inversion_weight * (len(bbox_located) / len(snapshot))
            + self._policy.zone_interruption_weight,
        )
        score = None
        warnings: list[str] = []
        if assessed:
            observed_penalty = (
                self._policy.page_regression_weight * page_regression_ratio
                + self._policy.bbox_inversion_weight * bbox_inversion_ratio
                + self._policy.zone_interruption_weight * zone_interruption_ratio
            )
            penalty = observed_penalty + self._policy.unassessed_signal_penalty * (1.0 - assessment_coverage)
            score = max(0.0, 1.0 - penalty)
            if 1.0 - observed_penalty < self._policy.low_quality_warning_threshold:
                warnings.append("low observed reading-order quality")
            if assessment_coverage < self._policy.low_assessment_coverage_warning_threshold:
                warnings.append("limited reading-order assessment coverage")
        else:
            warnings.append("reading-order quality could not be assessed from source anchors")

        metrics = OrderQualityMetrics(
            element_count=len(snapshot),
            page_located_element_count=len(page_located),
            bbox_located_element_count=len(bbox_located),
            page_coverage_ratio=len(page_located) / len(snapshot),
            bbox_coverage_ratio=len(bbox_located) / len(snapshot),
            comparable_page_pair_count=len(page_pairs),
            page_regression_count=page_regressions,
            page_regression_ratio=page_regression_ratio,
            comparable_bbox_pair_count=len(bbox_pairs),
            bbox_inversion_count=bbox_inversions,
            bbox_inversion_ratio=bbox_inversion_ratio,
            zone_transition_count=zone_transitions,
            zone_interruption_count=zone_interruptions,
            zone_interruption_ratio=zone_interruption_ratio,
            assessed=assessed,
            assessment_coverage=assessment_coverage,
        )
        return OrderQualityReport(
            policy=self._policy,
            metrics=metrics,
            quality=DocumentQuality(
                order_quality=score,
                warnings=tuple(warnings),
                metrics={
                    "order_quality_version": self.version,
                    "order_quality_policy_version": self._policy.version,
                    "order_page_regression_ratio": page_regression_ratio,
                    "order_bbox_inversion_ratio": bbox_inversion_ratio,
                    "order_zone_interruption_ratio": zone_interruption_ratio,
                    "order_assessed": assessed,
                    "order_assessment_coverage": assessment_coverage,
                },
            ),
        )

    def _horizontally_comparable(self, left: BoundingBox, right: BoundingBox) -> bool:
        overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
        minimum_width = min(left.x1 - left.x0, right.x1 - right.x0)
        return minimum_width > 0 and overlap / minimum_width >= self._policy.horizontal_overlap_threshold

    @staticmethod
    def _zone(element: Element) -> str:
        if element.type == ElementType.HEADER:
            return "header"
        if element.type in {ElementType.FOOTER, ElementType.PAGE_NUMBER, ElementType.FOOTNOTE}:
            return "footer"
        try:
            explicit = source_zone(element)
        except SourceAttributeError as exc:
            raise OrderQualityError(str(exc)) from exc
        return explicit.casefold() if explicit is not None else "body"


__all__ = [
    "ORDER_QUALITY_ESTIMATOR_VERSION",
    "OrderQualityError",
    "OrderQualityEstimator",
    "OrderQualityMetrics",
    "OrderQualityPolicy",
    "OrderQualityReport",
]
