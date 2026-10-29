from __future__ import annotations

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


def _observation(order: int, text: str, type_hint: str, page: int, y0: int) -> DeepDocObservation:
    return DeepDocObservation(
        source_id=f"pdf:{page}:{order}",
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


def test_book_output_round_trips_and_projects_citation_and_quality():
    parsed = DeepDocParseResult(
        parser_name="integration-test",
        parser_version="1",
        media_type="application/pdf",
        observations=(
            _observation(0, "CHƯƠNG 1", "HEADING", 1, 80),
            _observation(1, "PHƯÌNG TRÌNH được trình bày ở đây.", "PARAGRAPH", 1, 180),
            _observation(2, "A normal English paragraph follows.", "PARAGRAPH", 2, 100),
        ),
    )
    understood = SourceAdapterRunner().understand_bytes(
        b"%PDF-integration-source",
        adapter=DeepDocSourceAdapter(parsed),
        document_id="book-integration",
        source_name="book.pdf",
    ).understanding

    store = CanonicalArtifactStore(MemoryArtifactBlobBackend())
    stored = store.persist(understood.document)
    loaded = store.load(stored.reference)
    built = RetrievalUnitBuilder(lambda text: len(text.split())).build(loaded)
    projected = RetrievalUnitProjector(
        lambda text: text.casefold(),
        lambda text: text,
        tokenizer_version="integration-tokenizer-1",
    ).project(
        loaded,
        built.units,
        kb_id="kb-integration",
        artifact_reference=stored.reference,
        artifact_registry_id="a" * 64,
    )

    assert loaded == understood.document
    assert stored.reference.content_encoding.value == "gzip"
    assert projected.documents
    anomalous = next(row for row in projected.documents if row.has_text_anomaly)
    assert anomalous.text_anomaly_count == 1
    assert anomalous.quality_multiplier < 1.0
    assert anomalous.source_anchors[0].page == 1
    assert anomalous.canonical_artifact_ref.registry_id == "a" * 64
    assert anomalous.element_ids[0] in loaded.quality.lexical.element_anomaly_counts
