from __future__ import annotations

import json
import pytest
from datetime import UTC, datetime
from pathlib import Path

from rag.source_understanding.artifacts import CanonicalArtifactStore, MemoryArtifactBlobBackend
from rag.source_understanding.retrieval_units.projector import RetrievalUnitProjectionError
from rag.source_understanding.retrieval_units import RetrievalUnitProjector
from rag.source_understanding.schemas import (
    BoundingBox,
    CanonicalDocument,
    ContextNode,
    ContextNodeRef,
    DocumentQuality,
    Element,
    ElementType,
    LexicalAnomalyEvidence,
    LexicalAnomalyReason,
    LexicalLanguage,
    LexicalQuality,
    ProcessingManifest,
    Provenance,
    RetrievalUnit,
    SourceAnchor,
    SourceLocation,
    StructureSource,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CONTENT_HASH = "sha256:" + "1" * 64
_REGISTRY_ID = "3" * 64


def _element(element_id: str, order: int, text: str, page: int) -> Element:
    return Element(
        id=element_id,
        type=ElementType.PARAGRAPH,
        order=order,
        raw_text=text,
        normalized_text=text,
        location=SourceLocation(
            source=StructureSource.DERIVED,
            page=page,
            bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.8, y1=0.3),
        ),
        provenance=Provenance(
            source=StructureSource.DERIVED,
            extractor="test",
            extractor_version="1",
        ),
    )


def _document() -> CanonicalDocument:
    return CanonicalDocument(
        document_id="document-1",
        content_hash=_CONTENT_HASH,
        source_revision="revision-1",
        processing=ProcessingManifest(
            adapter_name="test",
            adapter_version="1",
            processed_at=datetime(2026, 8, 24, tzinfo=UTC),
        ),
        elements=(
            _element("element-1", 0, "First paragraph", 1),
            _element("element-2", 1, "Second paragraph", 2),
        ),
        context_nodes=(
            ContextNode(
                id="chapter-1",
                type="CHAPTER",
                label="Chapter One",
                level=1,
                source=StructureSource.EXPLICIT,
                confidence=1.0,
            ),
        ),
        quality=DocumentQuality(
            text_quality=0.8,
            order_quality=0.95,
            structure_quality=0.9,
            overall=0.85,
            lexical=LexicalQuality(
                version="1",
                detected_language=LexicalLanguage.ENGLISH,
                token_count=4,
                vietnamese_token_count=0,
                english_token_count=4,
                anomaly_count=1,
                anomaly_ratio=0.25,
                reason_counts={"ENGLISH_REPEATED_LETTER": 1},
                element_anomaly_counts={"element-1": 1},
                evidence=(
                    LexicalAnomalyEvidence(
                        element_id="element-1",
                        token="errrrror",
                        language=LexicalLanguage.ENGLISH,
                        reason=LexicalAnomalyReason.ENGLISH_REPEATED_LETTER,
                    ),
                ),
            ),
        ),
    )


def _unit(element_id: str, text: str, page: int) -> RetrievalUnit:
    return RetrievalUnit(
        id=f"ru-{element_id}",
        document_id="document-1",
        content_hash=_CONTENT_HASH,
        source_revision="revision-1",
        element_ids=(element_id,),
        retrieval_text=text,
        display_text=text,
        context_path=(
            ContextNodeRef(
                id="chapter-1",
                type="CHAPTER",
                label="Chapter One",
                source=StructureSource.EXPLICIT,
                confidence=1.0,
            ),
        ),
        source_anchors=(
            SourceAnchor(
                source_id="document-1",
                content_hash=_CONTENT_HASH,
                source_revision="revision-1",
                element_id=element_id,
                location_source=StructureSource.DERIVED,
                page=page,
                bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.8, y1=0.3),
            ),
        ),
        token_count=2,
        version="builder-1",
    )


def _artifact_reference():
    return CanonicalArtifactStore(MemoryArtifactBlobBackend()).persist(_document()).reference


def test_projector_orders_units_and_emits_only_rebuildable_index_fields():
    projector = RetrievalUnitProjector(
        lambda text: text.lower().replace(" ", "_"),
        lambda text: f"fine:{text}",
        tokenizer_version="test-tokenizer-1",
    )

    result = projector.project(
        _document(),
        (
            _unit("element-2", "Second paragraph", 2),
            _unit("element-1", "First paragraph", 1),
        ),
        kb_id="kb-1",
        artifact_reference=_artifact_reference(),
        artifact_registry_id=_REGISTRY_ID,
    )

    assert [item.id for item in result.documents] == ["ru-element-1", "ru-element-2"]
    projected = result.documents[0].to_index_dict()
    assert projected["id"] == projected["retrieval_unit_id"]
    assert projected["element_ids"] == ["element-1"]
    assert projected["content_ltks"] == "first_paragraph"
    assert projected["content_sm_ltks"] == "fine:first_paragraph"
    assert projected["context_path"][0]["id"] == "chapter-1"
    assert projected["source_anchors"][0]["page"] == 1
    assert projected["canonical_artifact_ref"] == {
        "registry_id": _REGISTRY_ID,
        "artifact_hash": _artifact_reference().artifact_hash,
        "schema_version": "1.2",
        "serialization_version": "1",
        "content_encoding": "gzip",
        "stored_hash": _artifact_reference().stored_hash,
        "stored_byte_size": _artifact_reference().stored_byte_size,
    }
    assert projected["language"] == "en"
    assert projected["docnm_kwd"] == "document-1"
    assert [row.chunk_order_int for row in result.documents] == [0, 1]
    assert projected["document_text_quality"] == 0.8
    assert projected["document_order_quality"] == 0.95
    assert projected["text_anomaly_count"] == 1
    assert projected["has_text_anomaly"] is True
    assert projected["quality_multiplier"] == 0.84
    assert "display_text" not in projected
    assert "metadata" not in projected
    assert "artifact_key" not in projected["canonical_artifact_ref"]


def test_projector_rejects_other_canonical_artifact_for_same_pdf():
    document = _document()
    changed = document.model_copy(update={"quality": DocumentQuality(overall=0.2)})
    projector = RetrievalUnitProjector(str.lower, str, tokenizer_version="test-1")
    with pytest.raises(RetrievalUnitProjectionError, match="exact document"):
        projector.project(changed, (), kb_id="kb-1", artifact_reference=_artifact_reference(), artifact_registry_id=_REGISTRY_ID)


def test_elasticsearch_opensearch_and_infinity_mappings_cover_projection_contract():
    elastic = json.loads((_PROJECT_ROOT / "conf" / "mapping.json").read_text(encoding="utf-8"))["mappings"]["properties"]
    opensearch = json.loads((_PROJECT_ROOT / "conf" / "os_mapping.json").read_text(encoding="utf-8"))["mappings"]["properties"]
    infinity = json.loads((_PROJECT_ROOT / "conf" / "infinity_mapping.json").read_text(encoding="utf-8"))

    for mapping in (elastic, opensearch):
        assert mapping["retrieval_unit_id"]["type"] == "keyword"
        assert mapping["element_ids"]["type"] == "keyword"
        assert mapping["context_path"]["type"] == "nested"
        assert mapping["source_anchors"]["type"] == "nested"
        assert mapping["canonical_artifact_ref"]["properties"]["registry_id"]["type"] == "keyword"
        assert mapping["language"]["type"] == "keyword"
        assert mapping["document_text_quality"]["type"] == "float"
        assert mapping["document_order_quality"]["type"] == "float"
        assert mapping["text_anomaly_count"]["type"] == "integer"
        assert mapping["has_text_anomaly"]["type"] == "boolean"
        assert mapping["quality_multiplier"]["type"] == "float"

    for field in ("element_ids", "logical_unit_ids", "context_path", "source_anchors", "canonical_artifact_ref"):
        assert infinity[field]["type"] == "json"
    assert infinity["language"]["type"] == "varchar"
    assert infinity["document_order_quality"]["type"] == "float"
    assert infinity["has_text_anomaly"]["type"] == "bool"
    assert infinity["quality_multiplier"]["type"] == "float"

    oceanbase_source = (_PROJECT_ROOT / "rag" / "utils" / "ob_conn.py").read_text(encoding="utf-8")
    for field in ("retrieval_unit_id", "element_ids", "context_path", "source_anchors", "canonical_artifact_ref", "language", "has_text_anomaly", "quality_multiplier"):
        assert f'Column("{field}"' in oceanbase_source
