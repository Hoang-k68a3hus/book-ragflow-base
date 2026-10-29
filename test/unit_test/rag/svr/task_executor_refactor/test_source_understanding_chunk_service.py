from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from deepdoc.parser.source_observation import DeepDocExtractionSource, DeepDocObservation, DeepDocObservationKind, DeepDocParseResult
from rag.source_understanding.artifacts import CanonicalArtifactStore, MemoryArtifactBlobBackend

from rag.svr.task_executor_refactor.chunk_service import ChunkService
from rag.svr.task_executor_refactor.source_understanding_service import (
    BookSourceUnderstandingIngestionResult,
    WholeDocumentTaskRequiredError,
    _validate_whole_document_task,
    ingest_book_pdf,
)
from test.unit_test.rag.svr.task_executor_refactor.conftest import make_task_context


@pytest.mark.asyncio
async def test_book_pdf_feature_uses_projection_without_rewriting_retrieval_ids():
    ctx = make_task_context(
        parser_id="book",
        name="book.pdf",
        parser_config={
            "chunk_token_num": 512,
            "source_understanding": {"enabled": True},
        },
        kb_parser_config={},
        from_page=0,
        to_page=20,
    )
    ctx.get.return_value = 1_700_000_000_000
    projected = {
        "id": "retrieval-unit-1",
        "retrieval_unit_id": "retrieval-unit-1",
        "doc_id": "doc_1",
        "kb_id": "kb_1",
        "content_with_weight": "Chapter text",
        "content_ltks": "chapter text",
        "content_sm_ltks": "chapter text",
    }
    ingestion = BookSourceUnderstandingIngestionResult(
        documents=(projected,),
        canonical_artifact_registry_id="a" * 64,
        artifact_created=True,
        registry_created=True,
        canonical_element_count=2,
        retrieval_unit_count=1,
        unresolved_integrity_element_ids=(),
    )

    with patch(
        "rag.svr.task_executor_refactor.chunk_service.thread_pool_exec",
        new=AsyncMock(return_value=ingestion),
    ) as run_sync:
        result = await ChunkService(ctx).build_chunks(
            b"pdf bytes",
            storage_bucket="source-bucket",
        )

    assert result == [projected]
    assert result[0]["id"] == result[0]["retrieval_unit_id"]
    call = run_sync.await_args
    assert call.kwargs["storage_bucket"] == "source-bucket"
    assert call.kwargs["from_page"] == 0
    assert call.kwargs["to_page"] == 20


def test_source_ingestion_rejects_partial_page_task():
    with (
        patch(
            "rag.svr.task_executor_refactor.source_understanding_service.book.Pdf.total_page_number",
            return_value=100,
        ),
        pytest.raises(WholeDocumentTaskRequiredError),
    ):
        _validate_whole_document_task(
            filename="book.pdf",
            binary=b"pdf bytes",
            from_page=0,
            to_page=12,
        )


def test_book_plain_text_selection_does_not_construct_visual_parser():
    from rag.app import book

    parsed = SimpleNamespace(attributes={"page_count": 2}, parser_name="poppler-pdftotext")
    native = Mock()
    native.extract_source.return_value = parsed
    with (
        patch("deepdoc.parser.PdfTextParser", return_value=native),
        patch.object(book, "Pdf", side_effect=AssertionError("visual parser must not be constructed")),
    ):
        result = book.extract_source("book.pdf", binary=b"source", parser_config={"layout_recognize": "Plain Text"})
    assert result is parsed
    native.extract_source.assert_called_once_with(b"source")


@pytest.mark.parametrize("budget", [0, -1, True, "512"])
def test_invalid_budget_fails_before_extraction_or_storage(budget):
    with patch("rag.svr.task_executor_refactor.source_understanding_service.book.extract_source") as extract:
        with pytest.raises(ValueError, match="chunk_token_num"):
            ingest_book_pdf(
                filename="book.pdf",
                binary=b"pdf bytes",
                document_id="doc-1",
                kb_id="kb-1",
                tenant_id="tenant-1",
                storage_bucket="bucket-1",
                parser_config={"chunk_token_num": budget},
                language="English",
                from_page=0,
                to_page=-1,
                document_create_time=None,
                callback=lambda **kwargs: None,
            )
        extract.assert_not_called()


def test_ingestion_projects_exact_artifact_and_keeps_global_tokenizer_language():
    from rag.svr.task_executor_refactor import source_understanding_service as service

    parsed = DeepDocParseResult(
        parser_name="test",
        parser_version="1",
        media_type="application/pdf",
        observations=(
            DeepDocObservation(
                source_id="paragraph-1",
                order=0,
                kind=DeepDocObservationKind.TEXT,
                text="English and tiếng Việt.",
                extraction_source=DeepDocExtractionSource.NATIVE,
                type_hint="PARAGRAPH",
            ),
        ),
    )
    documents = []

    def persist(document, **kwargs):
        documents.append(document)
        stored = CanonicalArtifactStore(MemoryArtifactBlobBackend()).persist(document)
        return SimpleNamespace(reference=stored.reference, registry_id="a" * 64, artifact_created=True, registry_created=True)

    original = service.rag_tokenizer.tokenizer
    stemmer = original.stemmer
    callback = Mock()
    with (
        patch.object(service.book.Pdf, "total_page_number", return_value=1),
        patch.object(service.book, "extract_source", return_value=parsed),
        patch.object(service.CanonicalArtifactPersistenceService, "persist", side_effect=persist),
    ):
        result = service.ingest_book_pdf(
            filename="bilingual.pdf",
            binary=b"fixture",
            document_id="doc-1",
            kb_id="kb-1",
            tenant_id="tenant-1",
            storage_bucket="bucket-1",
            parser_config={"chunk_token_num": 1},
            language="English",
            from_page=0,
            to_page=-1,
            document_create_time=None,
            callback=callback,
        )
    assert original.stemmer is stemmer
    assert documents[0].processing.processed_at == datetime(1970, 1, 1, tzinfo=UTC)
    assert result.documents[0]["docnm_kwd"] == "bilingual.pdf"
    assert result.documents[0]["tokenizer_version"] == service.RAGFLOW_RETRIEVAL_TOKENIZER_VERSION
    assert result.oversized_unit_ids
    assert any("exceed the token target" in call.kwargs.get("msg", "") for call in callback.call_args_list)
