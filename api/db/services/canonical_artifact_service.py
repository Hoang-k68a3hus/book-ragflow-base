from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from peewee import IntegrityError

from api.db.db_models import DB, CanonicalDocumentArtifact
from common import settings
from rag.source_understanding.artifacts import (
    ArtifactBlobBackend,
    ArtifactStoreError,
    CanonicalArtifactReference,
    CanonicalArtifactStore,
)
from rag.source_understanding.schemas.document import CanonicalDocument


class CanonicalArtifactRegistryConflictError(RuntimeError):
    """One logical source revision is already bound to another artifact."""


class CanonicalArtifactRegistryNotFoundError(LookupError):
    pass


@runtime_checkable
class RAGFlowObjectStorage(Protocol):
    def put(self, bucket: str, name: str, data: bytes, tenant_id: str | None = None) -> object: ...

    def get(self, bucket: str, name: str, tenant_id: str | None = None) -> bytes | None: ...

    def obj_exist(self, bucket: str, name: str, tenant_id: str | None = None) -> bool: ...


class RAGFlowArtifactBlobBackend(ArtifactBlobBackend):
    """Narrow adapter over the configured RAGFlow object-storage implementation."""

    def __init__(
        self,
        storage: RAGFlowObjectStorage,
        *,
        bucket: str,
        tenant_id: str,
    ) -> None:
        if not isinstance(storage, RAGFlowObjectStorage):
            raise TypeError("RAGFlow artifact backend requires put/get/obj_exist storage methods")
        self._storage = storage
        self._bucket = _validated_text("bucket", bucket, max_length=255)
        self._tenant_id = _validated_text("tenant_id", tenant_id, max_length=32)

    def create(self, key: str, data: bytes) -> bool:
        if self._storage.obj_exist(self._bucket, key, self._tenant_id):
            return False
        self._storage.put(self._bucket, key, data, self._tenant_id)
        return True

    def read(self, key: str) -> bytes | None:
        data = self._storage.get(self._bucket, key, self._tenant_id)
        if data is None:
            return None
        if not isinstance(data, bytes):
            raise ArtifactStoreError("RAGFlow object storage returned non-byte artifact data")
        return data


@dataclass(frozen=True, slots=True)
class CanonicalArtifactPersistenceResult:
    reference: CanonicalArtifactReference
    registry_id: str
    artifact_created: bool
    registry_created: bool


class CanonicalArtifactRegistryService:
    """Create-only DB registry; mutation and deletion are intentionally absent."""

    model = CanonicalDocumentArtifact

    @classmethod
    @DB.connection_context()
    def register(
        cls,
        *,
        tenant_id: str,
        document_id: str,
        source_revision: str | None,
        storage_bucket: str,
        reference: CanonicalArtifactReference,
    ) -> tuple[CanonicalDocumentArtifact, bool]:
        normalized_tenant = _validated_text("tenant_id", tenant_id, max_length=32)
        normalized_document = _validated_text("document_id", document_id, max_length=32)
        normalized_revision = _validated_optional_text("source_revision", source_revision, max_length=256)
        normalized_bucket = _validated_text("storage_bucket", storage_bucket, max_length=255)
        registry_id = canonical_artifact_registry_id(
            tenant_id=normalized_tenant,
            document_id=normalized_document,
            source_revision=normalized_revision,
            content_hash=reference.content_hash,
            schema_version=reference.schema_version,
        )
        expected = {
            "id": registry_id,
            "tenant_id": normalized_tenant,
            "document_id": normalized_document,
            "source_revision": normalized_revision,
            "content_hash": reference.content_hash,
            "schema_version": reference.schema_version,
            "serialization_version": reference.serialization_version,
            "artifact_hash": reference.artifact_hash,
            "storage_bucket": normalized_bucket,
            "artifact_key": reference.key,
            "media_type": reference.media_type,
            "byte_size": reference.byte_size,
            "content_encoding": reference.content_encoding.value,
            "stored_hash": reference.stored_hash,
            "stored_byte_size": reference.stored_byte_size,
        }
        try:
            record = cls.model.create(**expected)
        except IntegrityError:
            record = cls.model.get_or_none(cls.model.id == registry_id)
            if record is None:
                raise
            cls._assert_same_artifact(record, expected)
            return record, False
        return record, True

    @classmethod
    @DB.connection_context()
    def get(cls, registry_id: str, *, tenant_id: str) -> CanonicalDocumentArtifact:
        normalized_id = _validated_text("registry_id", registry_id, max_length=64)
        normalized_tenant = _validated_text("tenant_id", tenant_id, max_length=32)
        record = cls.model.get_or_none((cls.model.id == normalized_id) & (cls.model.tenant_id == normalized_tenant))
        if record is None:
            raise CanonicalArtifactRegistryNotFoundError(f"canonical artifact registry entry {normalized_id!r} does not exist")
        return record

    @staticmethod
    def reference(record: CanonicalDocumentArtifact) -> CanonicalArtifactReference:
        return CanonicalArtifactReference(
            key=record.artifact_key,
            content_hash=record.content_hash,
            schema_version=record.schema_version,
            artifact_hash=record.artifact_hash,
            byte_size=record.byte_size,
            content_encoding=record.content_encoding,
            stored_hash=record.stored_hash,
            stored_byte_size=record.stored_byte_size,
            media_type=record.media_type,
            serialization_version=record.serialization_version,
        )

    @staticmethod
    def _assert_same_artifact(
        record: CanonicalDocumentArtifact,
        expected: dict[str, object],
    ) -> None:
        mismatches = [name for name, value in expected.items() if getattr(record, name) != value]
        if mismatches:
            raise CanonicalArtifactRegistryConflictError(f"canonical artifact registry identity {expected['id']!r} already points to different immutable fields: {mismatches}")


class CanonicalArtifactPersistenceService:
    """Persist canonical bytes first, then register their immutable reference."""

    @classmethod
    def persist(
        cls,
        document: CanonicalDocument,
        *,
        tenant_id: str,
        document_id: str,
        storage_bucket: str,
        storage: RAGFlowObjectStorage | None = None,
    ) -> CanonicalArtifactPersistenceResult:
        if document.document_id != document_id:
            raise ValueError("canonical document_id must match the RAGFlow document_id")
        backend = cls._backend(
            storage=storage,
            tenant_id=tenant_id,
            storage_bucket=storage_bucket,
        )
        stored = CanonicalArtifactStore(backend).persist(document)
        record, registry_created = CanonicalArtifactRegistryService.register(
            tenant_id=tenant_id,
            document_id=document_id,
            source_revision=document.source_revision,
            storage_bucket=storage_bucket,
            reference=stored.reference,
        )
        return CanonicalArtifactPersistenceResult(
            reference=stored.reference,
            registry_id=record.id,
            artifact_created=stored.created,
            registry_created=registry_created,
        )

    @classmethod
    def load(
        cls,
        registry_id: str,
        *,
        tenant_id: str,
        storage: RAGFlowObjectStorage | None = None,
    ) -> CanonicalDocument:
        record = CanonicalArtifactRegistryService.get(registry_id, tenant_id=tenant_id)
        backend = cls._backend(
            storage=storage,
            tenant_id=tenant_id,
            storage_bucket=record.storage_bucket,
        )
        return CanonicalArtifactStore(backend).load(CanonicalArtifactRegistryService.reference(record))

    @staticmethod
    def _backend(
        *,
        storage: RAGFlowObjectStorage | None,
        tenant_id: str,
        storage_bucket: str,
    ) -> RAGFlowArtifactBlobBackend:
        selected = storage if storage is not None else settings.STORAGE_IMPL
        if selected is None:
            raise RuntimeError("RAGFlow object storage is not initialized")
        return RAGFlowArtifactBlobBackend(
            selected,
            bucket=storage_bucket,
            tenant_id=tenant_id,
        )


def canonical_artifact_registry_id(
    *,
    tenant_id: str,
    document_id: str,
    source_revision: str | None,
    content_hash: str,
    schema_version: str,
) -> str:
    identity = [
        _validated_text("tenant_id", tenant_id, max_length=32),
        _validated_text("document_id", document_id, max_length=32),
        _validated_optional_text("source_revision", source_revision, max_length=256),
        _validated_sha256("content_hash", content_hash),
        _validated_text("schema_version", schema_version, max_length=64),
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_text(name: str, value: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be a trimmed non-blank string")
    if len(value) > max_length:
        raise ValueError(f"{name} must not exceed {max_length} characters")
    return value


def _validated_optional_text(name: str, value: str | None, *, max_length: int) -> str:
    if value is None or value == "":
        return ""
    return _validated_text(name, value, max_length=max_length)


def _validated_sha256(name: str, value: str) -> str:
    normalized = _validated_text(name, value, max_length=71)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", normalized) is None:
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return normalized


__all__ = [
    "CanonicalArtifactPersistenceResult",
    "CanonicalArtifactPersistenceService",
    "CanonicalArtifactRegistryConflictError",
    "CanonicalArtifactRegistryNotFoundError",
    "CanonicalArtifactRegistryService",
    "RAGFlowArtifactBlobBackend",
    "RAGFlowObjectStorage",
    "canonical_artifact_registry_id",
]
