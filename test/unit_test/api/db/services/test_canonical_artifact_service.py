from types import SimpleNamespace

import pytest

from api.db.db_models import CanonicalDocumentArtifact
from api.db.services.canonical_artifact_service import (
    CanonicalArtifactRegistryConflictError,
    CanonicalArtifactRegistryService,
    canonical_artifact_registry_id,
)


def test_registry_model_is_create_only_artifact_pointer():
    assert CanonicalDocumentArtifact._meta.table_name == "canonical_document_artifact"
    assert CanonicalDocumentArtifact.artifact_key.max_length == 4096
    assert CanonicalDocumentArtifact.content_hash.max_length == 71
    assert CanonicalDocumentArtifact.artifact_hash.max_length == 71
    assert CanonicalDocumentArtifact.content_encoding.max_length == 32
    assert CanonicalDocumentArtifact.stored_hash.max_length == 71


def test_registry_identity_is_deterministic_and_revision_scoped():
    values = {
        "tenant_id": "tenant-1",
        "document_id": "document-1",
        "source_revision": "revision-1",
        "content_hash": "sha256:" + "1" * 64,
        "schema_version": "1.1",
    }

    first = canonical_artifact_registry_id(**values)
    second = canonical_artifact_registry_id(**values)
    changed = canonical_artifact_registry_id(**{**values, "source_revision": "revision-2"})

    assert first == second
    assert len(first) == 64
    assert changed != first


def test_registry_identity_normalizes_missing_source_revision():
    values = {
        "tenant_id": "tenant-1",
        "document_id": "document-1",
        "content_hash": "sha256:" + "1" * 64,
        "schema_version": "1.1",
    }

    assert canonical_artifact_registry_id(source_revision=None, **values) == canonical_artifact_registry_id(
        source_revision="",
        **values,
    )


def test_registry_rejects_rebinding_immutable_identity():
    expected = {"id": "registry-1", "artifact_hash": "sha256:" + "1" * 64}
    existing = SimpleNamespace(id="registry-1", artifact_hash="sha256:" + "2" * 64)

    with pytest.raises(CanonicalArtifactRegistryConflictError, match="artifact_hash"):
        CanonicalArtifactRegistryService._assert_same_artifact(existing, expected)
