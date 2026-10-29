from __future__ import annotations

import pytest

from deepdoc.parser.source_observation import (
    DeepDocBoundingBox,
    DeepDocObservation,
    DeepDocObservationKind,
    DeepDocParseResult,
    DeepDocSourceLocation,
)


def _observation(*, source_id: str, order: int) -> DeepDocObservation:
    return DeepDocObservation(
        source_id=source_id,
        order=order,
        kind=DeepDocObservationKind.TEXT,
        text=source_id,
        locations=(
            DeepDocSourceLocation(
                page=1,
                page_width=200,
                page_height=300,
                bbox=DeepDocBoundingBox(x0=10, y0=20, x1=100, y1=40),
            ),
        ),
        attributes={"nested": {"values": [1, 2]}},
    )


def test_parse_result_requires_unique_ascending_observations():
    with pytest.raises(ValueError, match="unique ascending order"):
        DeepDocParseResult(
            parser_name="deepdoc-test",
            parser_version="1",
            media_type="application/pdf",
            observations=(
                _observation(source_id="second", order=1),
                _observation(source_id="first", order=0),
            ),
        )


def test_observation_attributes_are_recursively_immutable():
    observation = _observation(source_id="source-1", order=0)

    with pytest.raises(TypeError):
        observation.attributes["new"] = "value"

    nested = observation.attributes["nested"]
    assert nested["values"] == (1, 2)
    with pytest.raises(TypeError):
        nested["new"] = "value"


def test_location_rejects_invalid_page_dimensions():
    with pytest.raises(ValueError, match="finite and positive"):
        DeepDocSourceLocation(page=1, page_width=0, page_height=300)
