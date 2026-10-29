from __future__ import annotations

from rag.source_understanding.profiling.order_quality import OrderQualityEstimator
from rag.source_understanding.schemas.context import StructureSource
from rag.source_understanding.schemas.element import BoundingBox, Element, ElementType, Provenance, SourceLocation


def _element(element_id: str, order: int, page: int, y0: float, *, element_type: ElementType = ElementType.PARAGRAPH) -> Element:
    return Element(
        id=element_id,
        order=order,
        type=element_type,
        raw_text=element_id,
        normalized_text=element_id,
        location=SourceLocation(
            source=StructureSource.EXPLICIT,
            page=page,
            bbox=BoundingBox(x0=0.1, y0=y0, x1=0.9, y1=min(1.0, y0 + 0.05)),
        ),
        provenance=Provenance(source=StructureSource.EXPLICIT, extractor="test"),
    )


def test_order_quality_detects_page_and_vertical_regressions():
    report = OrderQualityEstimator().estimate(
        (
            _element("first", 0, 2, 0.1),
            _element("second", 1, 1, 0.2),
            _element("third", 2, 2, 0.4),
            _element("fourth", 3, 2, 0.1),
        )
    )

    assert report.metrics.page_regression_count == 1
    assert report.metrics.bbox_inversion_count == 1
    assert report.quality.order_quality is not None and report.quality.order_quality < 1.0


def test_order_quality_does_not_compare_independent_columns():
    base_left = _element("left", 0, 1, 0.8)
    left = Element.model_validate(
        {
            **base_left.model_dump(mode="python"),
            "location": {
                "source": StructureSource.EXPLICIT,
                "page": 1,
                "bbox": {"x0": 0.05, "y0": 0.8, "x1": 0.45, "y1": 0.85},
            },
        }
    )
    right = Element.model_validate(
        {
            **left.model_dump(mode="python"),
            "id": "right",
            "order": 1,
            "location": {
                "source": StructureSource.EXPLICIT,
                "page": 1,
                "bbox": {"x0": 0.55, "y0": 0.1, "x1": 0.95, "y1": 0.15},
            },
        }
    )

    report = OrderQualityEstimator().estimate((left, right))

    assert report.metrics.comparable_bbox_pair_count == 0
    assert report.metrics.bbox_inversion_count == 0
    assert report.quality.order_quality == 1.0
