from __future__ import annotations

import hashlib
import json

from pydantic import TypeAdapter

from rag.source_understanding.schemas.context import ContentHash, JsonObject

_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)
SEMANTIC_CONFIGURATION_FINGERPRINT_VERSION = "semantic-config-fingerprint:1"


def semantic_configuration_hash(configuration: object) -> ContentHash:
    """Hash one validated semantic configuration deterministically.

    Provider and annotator versions identify code, while this hash identifies
    the exact policy/configuration that materially produced annotations.
    """

    try:
        validated = _JSON_OBJECT_ADAPTER.validate_python(configuration)
    except Exception as exc:
        raise ValueError("semantic configuration must be a JSON-safe finite object") from exc
    encoded = json.dumps(
        validated,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def semantic_provider_capabilities_hash(
    capabilities: object,
) -> ContentHash:
    """Hash the complete declared capability surface, including ontology scopes."""

    from .provider import SemanticProviderCapabilities

    validated = SemanticProviderCapabilities.model_validate(capabilities)
    return semantic_configuration_hash(validated.model_dump(mode="json"))
