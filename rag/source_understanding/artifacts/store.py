from __future__ import annotations

import gzip
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import quote

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import ContentHash, SchemaModel
from rag.source_understanding.schemas.document import CanonicalDocument

from .serialization import (
    CANONICAL_DOCUMENT_MEDIA_TYPE,
    CANONICAL_DOCUMENT_SERIALIZATION_VERSION,
    CanonicalSerializationError,
    canonical_artifact_hash,
    deserialize_canonical_document,
    serialize_canonical_document,
)

CANONICAL_ARTIFACT_NAMESPACE = "canonical-documents"


class ArtifactContentEncoding(StrEnum):
    IDENTITY = "identity"
    GZIP = "gzip"


class ArtifactStoreError(RuntimeError):
    """Base error for canonical artifact persistence."""


class ArtifactNotFoundError(ArtifactStoreError):
    pass


class ArtifactIntegrityError(ArtifactStoreError):
    pass


class ImmutableArtifactConflictError(ArtifactStoreError):
    pass


class CanonicalArtifactReference(SchemaModel):
    """Stable reference stored by the DB registry and future search documents."""

    key: str = Field(min_length=1, max_length=4096)
    content_hash: ContentHash
    schema_version: str = Field(min_length=1, max_length=64)
    artifact_hash: ContentHash
    byte_size: int = Field(gt=0)
    content_encoding: ArtifactContentEncoding
    stored_hash: ContentHash
    stored_byte_size: int = Field(gt=0)
    media_type: str = Field(default=CANONICAL_DOCUMENT_MEDIA_TYPE, min_length=1, max_length=256)
    serialization_version: str = Field(default=CANONICAL_DOCUMENT_SERIALIZATION_VERSION, min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_supported_contract(self) -> CanonicalArtifactReference:
        if self.media_type != CANONICAL_DOCUMENT_MEDIA_TYPE:
            raise ValueError(f"unsupported canonical artifact media type {self.media_type!r}")
        if self.serialization_version != CANONICAL_DOCUMENT_SERIALIZATION_VERSION:
            raise ValueError(f"unsupported canonical serialization version {self.serialization_version!r}")
        return self


class StoredCanonicalArtifact(SchemaModel):
    reference: CanonicalArtifactReference
    created: bool


@runtime_checkable
class ArtifactBlobBackend(Protocol):
    """Minimal create-only blob boundary required by the immutable store."""

    def create(self, key: str, data: bytes) -> bool:
        """Create bytes when absent and return whether this call created them."""

    def read(self, key: str) -> bytes | None: ...


class MemoryArtifactBlobBackend:
    """Deterministic in-memory backend for focused tests and local composition."""

    def __init__(self, initial: Mapping[str, bytes] | None = None) -> None:
        self._objects = dict(initial or {})

    def create(self, key: str, data: bytes) -> bool:
        if key in self._objects:
            return False
        self._objects[key] = bytes(data)
        return True

    def read(self, key: str) -> bytes | None:
        value = self._objects.get(key)
        return bytes(value) if value is not None else None


class FileArtifactBlobBackend:
    """Create-only filesystem backend rooted at one explicit directory."""

    def __init__(self, root: Path) -> None:
        resolved = root.resolve()
        if not resolved.is_absolute():
            raise ValueError("artifact filesystem root must resolve to an absolute path")
        self._root = resolved

    def create(self, key: str, data: bytes) -> bool:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            return False
        return True

    def read(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _path(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")):
            raise ArtifactStoreError("artifact key must be relative")
        candidate = (self._root / key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactStoreError("artifact key escapes filesystem root") from exc
        return candidate


class CanonicalArtifactStore:
    def __init__(
        self,
        backend: ArtifactBlobBackend,
        *,
        namespace: str = CANONICAL_ARTIFACT_NAMESPACE,
        content_encoding: ArtifactContentEncoding = ArtifactContentEncoding.GZIP,
    ) -> None:
        if not isinstance(backend, ArtifactBlobBackend):
            raise TypeError("canonical artifact store requires an ArtifactBlobBackend")
        self._backend = backend
        self._namespace = _validate_namespace(namespace)
        self._content_encoding = content_encoding

    def persist(self, document: CanonicalDocument) -> StoredCanonicalArtifact:
        data = serialize_canonical_document(document)
        artifact_hash = canonical_artifact_hash(data)
        stored_data = self._encode(data)
        reference = CanonicalArtifactReference(
            key=self._artifact_key(
                content_hash=document.content_hash,
                schema_version=document.schema_version,
                artifact_hash=artifact_hash,
                content_encoding=self._content_encoding,
            ),
            content_hash=document.content_hash,
            schema_version=document.schema_version,
            artifact_hash=artifact_hash,
            byte_size=len(data),
            content_encoding=self._content_encoding,
            stored_hash=canonical_artifact_hash(stored_data),
            stored_byte_size=len(stored_data),
        )

        created = self._backend.create(reference.key, stored_data)
        persisted = self._backend.read(reference.key)
        if persisted is None:
            raise ArtifactStoreError(f"artifact backend did not persist {reference.key!r}")
        if persisted != stored_data:
            raise ImmutableArtifactConflictError(f"immutable canonical artifact {reference.key!r} already contains different bytes")
        return StoredCanonicalArtifact(reference=reference, created=created)

    def load(self, reference: CanonicalArtifactReference) -> CanonicalDocument:
        expected_key = self._artifact_key(
            content_hash=reference.content_hash,
            schema_version=reference.schema_version,
            artifact_hash=reference.artifact_hash,
            content_encoding=reference.content_encoding,
        )
        if reference.key != expected_key:
            raise ArtifactIntegrityError("canonical artifact key does not match its content-addressed registry fields")
        stored_data = self._backend.read(reference.key)
        if stored_data is None:
            raise ArtifactNotFoundError(f"canonical artifact {reference.key!r} does not exist")
        if len(stored_data) != reference.stored_byte_size:
            raise ArtifactIntegrityError(f"canonical artifact {reference.key!r} stored byte size does not match its registry reference")
        if canonical_artifact_hash(stored_data) != reference.stored_hash:
            raise ArtifactIntegrityError(f"canonical artifact {reference.key!r} stored hash does not match its registry reference")
        data = self._decode(stored_data, reference.content_encoding)
        if len(data) != reference.byte_size:
            raise ArtifactIntegrityError(f"canonical artifact {reference.key!r} canonical byte size does not match its registry reference")
        if canonical_artifact_hash(data) != reference.artifact_hash:
            raise ArtifactIntegrityError(f"canonical artifact {reference.key!r} canonical hash does not match its registry reference")
        try:
            document = deserialize_canonical_document(data)
        except CanonicalSerializationError as exc:
            raise ArtifactIntegrityError(f"canonical artifact {reference.key!r} is not canonical JSON") from exc
        if document.content_hash != reference.content_hash:
            raise ArtifactIntegrityError(f"canonical artifact {reference.key!r} source content hash does not match its registry reference")
        if document.schema_version != reference.schema_version:
            raise ArtifactIntegrityError(f"canonical artifact {reference.key!r} schema version does not match its registry reference")
        return document

    def _artifact_key(
        self,
        *,
        content_hash: str,
        schema_version: str,
        artifact_hash: str,
        content_encoding: ArtifactContentEncoding,
    ) -> str:
        schema_segment = quote(schema_version, safe="-._~")
        source_digest = content_hash.removeprefix("sha256:")
        artifact_digest = artifact_hash.removeprefix("sha256:")
        suffix = ".json.gz" if content_encoding == ArtifactContentEncoding.GZIP else ".json"
        return f"{self._namespace}/serialization-v{CANONICAL_DOCUMENT_SERIALIZATION_VERSION}/schema-{schema_segment}/source-sha256-{source_digest}/artifact-sha256-{artifact_digest}{suffix}"

    def _encode(self, data: bytes) -> bytes:
        if self._content_encoding == ArtifactContentEncoding.IDENTITY:
            return data
        return gzip.compress(data, compresslevel=6, mtime=0)

    @staticmethod
    def _decode(data: bytes, encoding: ArtifactContentEncoding) -> bytes:
        if encoding == ArtifactContentEncoding.IDENTITY:
            return data
        try:
            return gzip.decompress(data)
        except (EOFError, OSError) as exc:
            raise ArtifactIntegrityError("canonical artifact is not valid gzip data") from exc


def _validate_namespace(namespace: str) -> str:
    if not namespace or namespace.strip() != namespace:
        raise ValueError("artifact namespace must be trimmed and non-blank")
    if namespace.startswith(("/", "\\")) or namespace.endswith(("/", "\\")):
        raise ValueError("artifact namespace must be a relative path without a trailing separator")
    segments = namespace.replace("\\", "/").split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("artifact namespace contains an unsafe path segment")
    return "/".join(segments)


__all__ = [
    "CANONICAL_ARTIFACT_NAMESPACE",
    "ArtifactBlobBackend",
    "ArtifactContentEncoding",
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "ArtifactStoreError",
    "CanonicalArtifactReference",
    "CanonicalArtifactStore",
    "FileArtifactBlobBackend",
    "ImmutableArtifactConflictError",
    "MemoryArtifactBlobBackend",
    "StoredCanonicalArtifact",
]
