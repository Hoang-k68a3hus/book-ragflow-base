from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from rag.source_understanding.artifacts import (
    ArtifactContentEncoding,
    ArtifactIntegrityError,
    CanonicalArtifactStore,
    CanonicalSerializationError,
    FileArtifactBlobBackend,
    ImmutableArtifactConflictError,
    MemoryArtifactBlobBackend,
    canonical_artifact_hash,
    deserialize_canonical_document,
    serialize_canonical_document,
)
from rag.source_understanding.schemas.document import (
    CanonicalDocument,
    DocumentMetadata,
    ProcessingManifest,
)

_SOURCE_HASH = "sha256:" + "1" * 64


def _document(*, attributes: dict[str, object] | None = None) -> CanonicalDocument:
    return CanonicalDocument(
        document_id="document-1",
        content_hash=_SOURCE_HASH,
        source_revision="revision-1",
        processing=ProcessingManifest(
            adapter_name="test-adapter",
            adapter_version="1",
            processed_at=datetime(2026, 8, 24, 10, 30, tzinfo=UTC),
            configuration={"z": 2, "a": 1},
        ),
        metadata=DocumentMetadata(
            title="Sách tiếng Việt",
            attributes=attributes or {"z": 2, "a": 1},
        ),
    )


def test_serialization_is_deterministic_across_mapping_insertion_order():
    left = _document(attributes={"z": 2, "nested": {"b": 2, "a": 1}})
    right = _document(attributes={"nested": {"a": 1, "b": 2}, "z": 2})

    left_bytes = serialize_canonical_document(left)
    right_bytes = serialize_canonical_document(right)

    assert left_bytes == right_bytes
    assert left_bytes == serialize_canonical_document(deserialize_canonical_document(left_bytes))
    assert b"S\xc3\xa1ch ti\xe1\xba\xbfng Vi\xe1\xbb\x87t" in left_bytes
    assert b'": ' not in left_bytes
    assert b", " not in left_bytes


def test_deserializer_rejects_valid_but_noncanonical_json():
    canonical = serialize_canonical_document(_document())
    noncanonical = json.dumps(json.loads(canonical), ensure_ascii=False, indent=2).encode("utf-8")

    with pytest.raises(CanonicalSerializationError, match="not canonical encoding"):
        deserialize_canonical_document(noncanonical)


def test_artifact_store_is_idempotent_and_round_trips_document():
    backend = MemoryArtifactBlobBackend()
    store = CanonicalArtifactStore(backend)
    document = _document()

    first = store.persist(document)
    second = store.persist(document)

    assert first.created is True
    assert second.created is False
    assert first.reference == second.reference
    assert first.reference.content_hash == document.content_hash
    assert first.reference.schema_version == document.schema_version
    assert first.reference.artifact_hash == canonical_artifact_hash(serialize_canonical_document(document))
    assert first.reference.content_encoding == ArtifactContentEncoding.GZIP
    assert first.reference.stored_byte_size < first.reference.byte_size
    assert first.reference.key.endswith(".json.gz")
    assert store.load(first.reference) == document


def test_artifact_store_detects_corrupted_registered_bytes():
    document = _document()
    original = CanonicalArtifactStore(MemoryArtifactBlobBackend()).persist(document)
    corrupted_backend = MemoryArtifactBlobBackend({original.reference.key: b"{}"})

    with pytest.raises(ArtifactIntegrityError, match="stored byte size"):
        CanonicalArtifactStore(corrupted_backend).load(original.reference)


def test_artifact_store_never_overwrites_an_existing_key_with_other_bytes():
    document = _document()
    original = CanonicalArtifactStore(MemoryArtifactBlobBackend()).persist(document)
    occupied_backend = MemoryArtifactBlobBackend({original.reference.key: b"different"})

    with pytest.raises(ImmutableArtifactConflictError, match="different bytes"):
        CanonicalArtifactStore(occupied_backend).persist(document)


def test_filesystem_backend_creates_nested_content_addressed_artifact(tmp_path):
    store = CanonicalArtifactStore(FileArtifactBlobBackend(tmp_path))

    stored = store.persist(_document())

    assert (tmp_path / stored.reference.key).is_file()
    assert store.load(stored.reference) == _document()
