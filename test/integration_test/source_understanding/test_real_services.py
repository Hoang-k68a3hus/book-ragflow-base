"""Real RAGFlow persistence/search connectors with synthetic extraction input.

Run separately from unit tests that replace modules in sys.modules. Requires
configured MySQL, MinIO and Elasticsearch services. No LLM is used.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from deepdoc.parser.source_observation import (
    DeepDocBoundingBox,
    DeepDocExtractionSource,
    DeepDocObservation,
    DeepDocObservationKind,
    DeepDocParseResult,
    DeepDocSourceLocation,
)
from rag.source_understanding.adapters import DeepDocSourceAdapter, SourceAdapterRunner
from rag.source_understanding.artifacts import CanonicalArtifactStore, MemoryArtifactBlobBackend
from rag.source_understanding.retrieval_units import RetrievalUnitBuilder, RetrievalUnitProjector

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SOURCE_UNDERSTANDING_SERVICE_INTEGRATION") != "1",
    reason="requires configured MySQL, MinIO and Elasticsearch services",
)


def _observation(order: int, text: str, type_hint: str, page: int, y0: int) -> DeepDocObservation:
    return DeepDocObservation(
        source_id=f"integration:{page}:{order}",
        order=order,
        kind=DeepDocObservationKind.TEXT,
        text=text,
        extraction_source=DeepDocExtractionSource.NATIVE,
        type_hint=type_hint,
        locations=(
            DeepDocSourceLocation(
                page=page,
                page_width=600,
                page_height=900,
                bbox=DeepDocBoundingBox(x0=60, y0=y0, x1=540, y1=y0 + 40),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_canonical_artifact_and_retrieval_index_round_trip_on_real_services():
    from common import settings
    from common.token_utils import num_tokens_from_string
    from api.db.db_models import CanonicalDocumentArtifact
    from api.db.services.canonical_artifact_service import (
        CanonicalArtifactPersistenceService,
        CanonicalArtifactRegistryService,
        CanonicalArtifactRegistryConflictError,
        CanonicalArtifactRegistryNotFoundError,
    )
    from rag.nlp import rag_tokenizer
    from rag.nlp.search import Dealer

    settings.init_settings()
    assert settings.docStoreConn.db_type() == "elasticsearch"
    CanonicalDocumentArtifact.create_table(safe=True)
    run_id = uuid4().hex
    tenant_id = run_id
    index = f"ragflow-su-test-{run_id}"
    bucket = "canonical-integration"
    store = settings.docStoreConn
    persisted = None
    parsed = DeepDocParseResult(
        parser_name="service-integration-fixture",
        parser_version="1",
        media_type="application/pdf",
        observations=(
            _observation(0, "CHƯƠNG 1", "HEADING", 1, 80),
            _observation(1, "Phương trình được trình bày ở đây.", "PARAGRAPH", 1, 180),
            _observation(2, "A normal English paragraph follows.", "PARAGRAPH", 2, 100),
        ),
    )
    document = (
        SourceAdapterRunner()
        .understand_bytes(
            b"service integration fixture: not a PDF extraction test",
            adapter=DeepDocSourceAdapter(parsed),
            document_id=run_id,
            source_name="bilingual-book.pdf",
            processed_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        .understanding.document
    )
    expected_reference = CanonicalArtifactStore(MemoryArtifactBlobBackend()).persist(document).reference
    try:
        persisted = CanonicalArtifactPersistenceService.persist(
            document,
            tenant_id=tenant_id,
            document_id=run_id,
            storage_bucket=bucket,
        )
        again = CanonicalArtifactPersistenceService.persist(
            document,
            tenant_id=tenant_id,
            document_id=run_id,
            storage_bucket=bucket,
        )
        loaded = CanonicalArtifactPersistenceService.load(persisted.registry_id, tenant_id=tenant_id)
        assert loaded == document
        assert persisted.reference.content_encoding.value == "gzip"
        assert not again.artifact_created and not again.registry_created
        record = CanonicalDocumentArtifact.get_by_id(persisted.registry_id)
        assert record.create_time is not None
        assert record.stored_byte_size == persisted.reference.stored_byte_size
        with pytest.raises(CanonicalArtifactRegistryNotFoundError):
            CanonicalArtifactPersistenceService.load(persisted.registry_id, tenant_id="another-tenant")
        with pytest.raises(CanonicalArtifactRegistryConflictError):
            CanonicalArtifactRegistryService.register(
                tenant_id=tenant_id,
                document_id=run_id,
                source_revision=document.source_revision,
                storage_bucket=bucket,
                reference=persisted.reference.model_copy(update={"artifact_hash": "sha256:" + "0" * 64}),
            )
        units = RetrievalUnitBuilder(num_tokens_from_string).build(loaded).units
        projected = RetrievalUnitProjector(
            rag_tokenizer.tokenize,
            rag_tokenizer.fine_grained_tokenize,
            tokenizer_version="ragflow-rag-tokenizer-1",
        ).project(loaded, units, kb_id=run_id, artifact_reference=persisted.reference, artifact_registry_id=persisted.registry_id)
        assert projected.documents
        assert store.create_idx(index, run_id, 384)
        index_rows = [row.to_index_dict() for row in projected.documents]
        # Synthetic vectors validate the index contract; HTTP E2E uses real TEI.
        for row in index_rows:
            row["q_384_vec"] = [1.0] + [0.0] * 383
        assert store.insert(index_rows, index, run_id) == []
        vector_mapping = store.es.indices.get_mapping(index=index)[index]["mappings"]["properties"]["q_384_vec"]
        assert vector_mapping["type"] == "dense_vector"
        assert vector_mapping["dims"] == 384
        dealer = Dealer(store)
        all_rows = await dealer.search({"kb_ids": [run_id], "doc_ids": [run_id], "size": 20}, index, [run_id])
        assert all_rows.total == len(projected.documents)
        for fields in all_rows.field.values():
            assert isinstance(fields["canonical_artifact_ref"], dict)
            assert isinstance(fields["has_text_anomaly"], bool)
            assert isinstance(fields["document_order_quality"], float)
            assert fields["canonical_artifact_ref"]["registry_id"] == persisted.registry_id
            assert fields["docnm_kwd"] == "bilingual-book.pdf"
            assert set(fields["element_ids"]) <= {element.id for element in loaded.elements}
            assert fields["source_anchors"][0]["page"] >= 1
        for query in ("Phương trình", "English paragraph"):
            found = await dealer.search({"question": query, "kb_ids": [run_id], "size": 10}, index, [run_id])
            assert found.total > 0
            assert all(row["canonical_artifact_ref"]["registry_id"] == persisted.registry_id for row in found.field.values())
    finally:
        # Only delete resources owned by this run, never reset a shared index.
        store.delete_idx(index, "")
        if persisted is not None:
            CanonicalDocumentArtifact.delete().where(CanonicalDocumentArtifact.id == persisted.registry_id).execute()
        settings.STORAGE_IMPL.rm(bucket, expected_reference.key, tenant_id=tenant_id)
