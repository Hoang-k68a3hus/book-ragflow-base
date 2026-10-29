"""Deterministic and immutable persistence for canonical documents."""

from .serialization import (
    CANONICAL_DOCUMENT_MEDIA_TYPE,
    CANONICAL_DOCUMENT_SERIALIZATION_VERSION,
    CanonicalSerializationError,
    canonical_artifact_hash,
    deserialize_canonical_document,
    serialize_canonical_document,
)
from .store import (
    CANONICAL_ARTIFACT_NAMESPACE,
    ArtifactBlobBackend,
    ArtifactContentEncoding,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStoreError,
    CanonicalArtifactReference,
    CanonicalArtifactStore,
    FileArtifactBlobBackend,
    ImmutableArtifactConflictError,
    MemoryArtifactBlobBackend,
    StoredCanonicalArtifact,
)

__all__ = [
    "CANONICAL_ARTIFACT_NAMESPACE",
    "CANONICAL_DOCUMENT_MEDIA_TYPE",
    "CANONICAL_DOCUMENT_SERIALIZATION_VERSION",
    "ArtifactBlobBackend",
    "ArtifactContentEncoding",
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "ArtifactStoreError",
    "CanonicalArtifactReference",
    "CanonicalArtifactStore",
    "CanonicalSerializationError",
    "FileArtifactBlobBackend",
    "ImmutableArtifactConflictError",
    "MemoryArtifactBlobBackend",
    "StoredCanonicalArtifact",
    "canonical_artifact_hash",
    "deserialize_canonical_document",
    "serialize_canonical_document",
]
