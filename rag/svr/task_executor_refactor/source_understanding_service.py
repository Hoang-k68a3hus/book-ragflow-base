from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from api.db.services.canonical_artifact_service import (
    CanonicalArtifactPersistenceService,
)
from common.constants import MAXIMUM_PAGE_NUMBER
from common.token_utils import num_tokens_from_string
from rag.app import book
from rag.nlp import rag_tokenizer
from rag.source_understanding.adapters import DeepDocSourceAdapter, SourceAdapterRunner
from rag.source_understanding.retrieval_units import (
    RetrievalUnitBuilder,
    RetrievalUnitBuildPolicy,
    RetrievalUnitProjector,
)

RAGFLOW_RETRIEVAL_TOKENIZER_VERSION = "ragflow-rag-tokenizer-2"
_DETERMINISTIC_PROCESSING_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class WholeDocumentTaskRequiredError(ValueError):
    """Canonical book ingestion was invoked with only part of the source PDF."""


@dataclass(frozen=True, slots=True)
class BookSourceUnderstandingIngestionResult:
    documents: tuple[dict[str, object], ...]
    canonical_artifact_registry_id: str
    artifact_created: bool
    registry_created: bool
    canonical_element_count: int
    retrieval_unit_count: int
    unresolved_integrity_element_ids: tuple[str, ...]
    oversized_unit_ids: tuple[str, ...] = ()
    skipped_blank_element_ids: tuple[str, ...] = ()
    skipped_excluded_element_ids: tuple[str, ...] = ()


def ingest_book_pdf(
    *,
    filename: str,
    binary: bytes,
    document_id: str,
    kb_id: str,
    tenant_id: str,
    storage_bucket: str,
    parser_config: Mapping[str, Any],
    language: str,
    from_page: int,
    to_page: int,
    document_create_time: int | None,
    callback: Callable[..., None],
) -> BookSourceUnderstandingIngestionResult:
    """Understand one complete book and return index-ready retrieval rows."""

    if not isinstance(binary, bytes) or not binary:
        raise ValueError("source-understanding book ingestion requires non-empty source bytes")
    if not filename.casefold().endswith(".pdf"):
        raise NotImplementedError("source-understanding book ingestion currently supports PDF only")
    max_tokens = parser_config.get("chunk_token_num", 512)
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("parser_config.chunk_token_num must be a positive integer")

    effective_to_page = MAXIMUM_PAGE_NUMBER if to_page < 0 else to_page
    _validate_whole_document_task(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=effective_to_page,
    )

    parsed = book.extract_source(
        filename,
        binary=binary,
        from_page=from_page,
        to_page=effective_to_page,
        lang=language,
        callback=callback,
        parser_config=dict(parser_config),
    )
    callback(msg=f"Build canonical document from typed {parsed.parser_name} observations.")
    adapted = SourceAdapterRunner().understand_bytes(
        binary,
        adapter=DeepDocSourceAdapter(parsed),
        document_id=document_id,
        source_name=filename,
        processed_at=_stable_processing_time(document_create_time),
    )
    canonical_document = adapted.understanding.document

    built = RetrievalUnitBuilder(
        num_tokens_from_string,
        policy=RetrievalUnitBuildPolicy(max_tokens=max_tokens),
    ).build(canonical_document)
    if not built.units:
        raise ValueError("source-understanding produced no retrievable text; inspect extraction and integrity diagnostics")
    if built.unresolved_integrity_element_ids or built.oversized_unit_ids:
        callback(
            msg=(
                f"Retrieval coverage warning: {len(built.unresolved_integrity_element_ids)} elements unresolved; "
                f"{len(built.oversized_unit_ids)} units exceed the token target. Source content is retained in the canonical artifact."
            )
        )

    callback(msg="Persist immutable canonical document artifact.")
    persisted = CanonicalArtifactPersistenceService.persist(
        canonical_document,
        tenant_id=tenant_id,
        document_id=document_id,
        storage_bucket=storage_bucket,
    )

    # Share the read-only dictionary, but never mutate another task's language.
    tokenizer = copy(rag_tokenizer.tokenizer)
    tokenizer.set_language("English")
    tokenizer.set_language(language)
    projected = RetrievalUnitProjector(
        tokenizer.tokenize,
        tokenizer.fine_grained_tokenize,
        tokenizer_version=RAGFLOW_RETRIEVAL_TOKENIZER_VERSION,
    ).project(
        canonical_document,
        built.units,
        kb_id=kb_id,
        artifact_reference=persisted.reference,
        artifact_registry_id=persisted.registry_id,
    )
    callback(msg=f"Projected {len(projected.documents)} canonical retrieval units.")

    return BookSourceUnderstandingIngestionResult(
        documents=tuple(item.to_index_dict() for item in projected.documents),
        canonical_artifact_registry_id=persisted.registry_id,
        artifact_created=persisted.artifact_created,
        registry_created=persisted.registry_created,
        canonical_element_count=len(canonical_document.elements),
        retrieval_unit_count=len(projected.documents),
        unresolved_integrity_element_ids=built.unresolved_integrity_element_ids,
        oversized_unit_ids=built.oversized_unit_ids,
        skipped_blank_element_ids=built.skipped_blank_element_ids,
        skipped_excluded_element_ids=built.skipped_excluded_element_ids,
    )


def _validate_whole_document_task(
    *,
    filename: str,
    binary: bytes,
    from_page: int,
    to_page: int,
) -> None:
    if from_page != 0:
        raise WholeDocumentTaskRequiredError("source-understanding book ingestion requires from_page=0; requeue the document")
    total_pages = book.Pdf.total_page_number(filename, binary)
    if total_pages is not None and total_pages > 0 and to_page < total_pages:
        raise WholeDocumentTaskRequiredError(f"source-understanding book ingestion requires one task covering every PDF page; task ends at {to_page}, PDF has {total_pages} pages")


def _stable_processing_time(document_create_time: int | None) -> datetime:
    """Keep immutable artifact bytes stable across retries and reparses."""

    if not isinstance(document_create_time, int) or isinstance(document_create_time, bool) or document_create_time <= 0:
        return _DETERMINISTIC_PROCESSING_EPOCH
    return datetime.fromtimestamp(document_create_time / 1000, tz=UTC)


__all__ = [
    "RAGFLOW_RETRIEVAL_TOKENIZER_VERSION",
    "BookSourceUnderstandingIngestionResult",
    "WholeDocumentTaskRequiredError",
    "ingest_book_pdf",
]
