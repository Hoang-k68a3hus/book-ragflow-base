from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SOURCE_UNDERSTANDING_CONFIG_KEY = "source_understanding"


class SourceUnderstandingFeatureConfigurationError(ValueError):
    """The ingestion feature flag has an unsupported persisted shape."""


def source_understanding_enabled(parser_config: Mapping[str, Any] | None) -> bool:
    """Return the explicit parser-scoped flag; absence always means disabled."""

    if parser_config is None:
        return False
    if not isinstance(parser_config, Mapping):
        raise SourceUnderstandingFeatureConfigurationError("parser_config must be a mapping")

    value = parser_config.get(SOURCE_UNDERSTANDING_CONFIG_KEY)
    if value is None:
        return False
    if not isinstance(value, Mapping):
        raise SourceUnderstandingFeatureConfigurationError(
            f"parser_config.{SOURCE_UNDERSTANDING_CONFIG_KEY} must be an object"
        )

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise SourceUnderstandingFeatureConfigurationError(
            f"parser_config.{SOURCE_UNDERSTANDING_CONFIG_KEY}.enabled must be a boolean"
        )
    return enabled


def book_source_understanding_enabled(
    parser_id: str,
    parser_config: Mapping[str, Any] | None,
) -> bool:
    """Gate the first production slice to the existing ``book`` parser."""

    return parser_id.casefold() == "book" and source_understanding_enabled(parser_config)


def book_pdf_source_understanding_enabled(
    parser_id: str,
    parser_config: Mapping[str, Any] | None,
    *,
    filename: str,
) -> bool:
    """Use the PR5 pipeline only where the typed DeepDOC PDF adapter exists."""

    return book_source_understanding_enabled(parser_id, parser_config) and filename.casefold().endswith(".pdf")


__all__ = [
    "SOURCE_UNDERSTANDING_CONFIG_KEY",
    "SourceUnderstandingFeatureConfigurationError",
    "book_pdf_source_understanding_enabled",
    "book_source_understanding_enabled",
    "source_understanding_enabled",
]
