from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from rag.source_understanding.schemas.document import CanonicalDocument

CANONICAL_DOCUMENT_MEDIA_TYPE = "application/vnd.ragflow.canonical-document+json"
CANONICAL_DOCUMENT_SERIALIZATION_VERSION = "1"


class CanonicalSerializationError(ValueError):
    """Canonical document bytes violate the deterministic JSON contract."""


def serialize_canonical_document(document: CanonicalDocument) -> bytes:
    """Serialize a canonical document to stable, whitespace-free UTF-8 JSON.

    Source strings are preserved byte-for-byte after UTF-8 encoding. Object keys
    are sorted recursively; array order remains canonical document order.
    """

    if not isinstance(document, CanonicalDocument):
        raise TypeError("canonical serialization requires CanonicalDocument")
    payload = document.model_dump(mode="json")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalSerializationError("canonical document contains a non-JSON value") from exc
    return encoded.encode("utf-8")


def deserialize_canonical_document(
    data: bytes,
    *,
    require_canonical: bool = True,
) -> CanonicalDocument:
    """Validate canonical JSON bytes and reconstruct the immutable schema."""

    if not isinstance(data, bytes):
        raise TypeError("canonical deserialization requires bytes")
    if not data:
        raise CanonicalSerializationError("canonical document artifact is empty")
    try:
        document = CanonicalDocument.model_validate_json(data)
    except (ValidationError, ValueError) as exc:
        raise CanonicalSerializationError("canonical document artifact is invalid") from exc
    if require_canonical and serialize_canonical_document(document) != data:
        raise CanonicalSerializationError("canonical document artifact is valid JSON but not canonical encoding")
    return document


def canonical_artifact_hash(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise TypeError("artifact hashing requires bytes")
    return "sha256:" + hashlib.sha256(data).hexdigest()


__all__ = [
    "CANONICAL_DOCUMENT_MEDIA_TYPE",
    "CANONICAL_DOCUMENT_SERIALIZATION_VERSION",
    "CanonicalSerializationError",
    "canonical_artifact_hash",
    "deserialize_canonical_document",
    "serialize_canonical_document",
]
