from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import model_validator

from rag.source_understanding.atomic.preservation import (
    ParserPreservationReport,
    evaluate_parser_preservation,
)
from rag.source_understanding.completion import UnderstandingCompletionBuilder
from rag.source_understanding.pipeline import SourceUnderstandingPipeline, SourceUnderstandingResult
from rag.source_understanding.schemas.context import Identifier, SchemaModel
from rag.source_understanding.schemas.document import ContentRegion, DocumentQuality
from rag.source_understanding.schemas.relation import Relation

from .base import (
    SOURCE_ADAPTER_PROTOCOL_VERSION,
    AdapterError,
    SourceAdapter,
    SourceAdapterResult,
    validate_adapter,
)

SOURCE_ADAPTER_RUNNER_VERSION = "3"


class AdaptedSourceUnderstandingResult(SchemaModel):
    version: str = SOURCE_ADAPTER_RUNNER_VERSION
    document_id: Identifier
    adapter_result: SourceAdapterResult
    preservation_report: ParserPreservationReport
    understanding: SourceUnderstandingResult

    @model_validator(mode="after")
    def validate_preservation(self) -> AdaptedSourceUnderstandingResult:
        if self.preservation_report.raw_element_count != len(self.adapter_result.raw_elements):
            raise ValueError("preservation_report raw count does not match adapter_result")
        if self.preservation_report.canonical_element_count != len(self.understanding.structural_document.elements):
            raise ValueError("preservation_report canonical count does not match structural document")
        if not self.preservation_report.fully_preserved:
            raise ValueError("adapted source result cannot carry a failed preservation audit")
        return self


class SourceAdapterRunner:
    """Bridge exact source bytes into the format-agnostic understanding pipeline.

    The adapter remains responsible only for source-near extraction. The runner
    verifies adapter identity/hash/protocol, then delegates normalization and all
    structural interpretation, including completion reporting, to one
    ``SourceUnderstandingPipeline`` instance.
    """

    version = SOURCE_ADAPTER_RUNNER_VERSION

    def __init__(
        self,
        pipeline: SourceUnderstandingPipeline | None = None,
        *,
        completion_builder: UnderstandingCompletionBuilder | None = None,
    ) -> None:
        if pipeline is not None and completion_builder is not None:
            raise ValueError("completion_builder cannot be supplied with an existing pipeline; configure completion ownership on the pipeline instead")
        self._pipeline = pipeline if pipeline is not None else SourceUnderstandingPipeline(completion_builder=completion_builder)

    def understand_bytes(
        self,
        data: bytes,
        *,
        adapter: SourceAdapter,
        document_id: str,
        source_name: str | None = None,
        source_revision: str | None = None,
        processed_at: datetime | None = None,
        base_quality: DocumentQuality | None = None,
        regions: Sequence[ContentRegion] = (),
        additional_relations: Sequence[Relation] = (),
    ) -> AdaptedSourceUnderstandingResult:
        if not isinstance(data, (bytes, bytearray)):
            raise AdapterError("source adapter runner requires exact source bytes")
        payload = bytes(data)
        name, version, media_types, _extensions = validate_adapter(adapter)
        adapted = adapter.adapt(payload, source_name=source_name)
        self._validate_adapter_result(
            adapted,
            payload=payload,
            adapter_name=name,
            adapter_version=version,
            media_types=media_types,
        )
        if not adapted.raw_elements:
            diagnostic_codes = tuple(diagnostic.code for diagnostic in adapted.diagnostics)
            raise AdapterError("source adapter produced no raw elements for understanding" + (f"; diagnostics={diagnostic_codes}" if diagnostic_codes else ""))
        timestamp = processed_at if processed_at is not None else datetime.now(UTC)
        processing = adapted.processing_manifest(processed_at=timestamp)
        understanding = self._pipeline.understand_raw(
            document_id=document_id,
            content_hash=adapted.content_hash,
            processing=processing,
            raw_elements=adapted.raw_elements,
            source_revision=source_revision,
            metadata=adapted.metadata,
            base_quality=base_quality,
            regions=regions,
            assets=adapted.assets,
            additional_relations=additional_relations,
            adapter_diagnostics=adapted.diagnostics,
        )
        preservation_report = evaluate_parser_preservation(
            adapted.raw_elements,
            understanding.structural_document.elements,
        )
        if not preservation_report.fully_preserved:
            details = "; ".join(preservation_report.issues[:5])
            raise AdapterError("parser preservation audit failed after normalization" + (f": {details}" if details else ""))
        return AdaptedSourceUnderstandingResult(
            document_id=document_id,
            adapter_result=adapted,
            preservation_report=preservation_report,
            understanding=understanding,
        )

    @staticmethod
    def _validate_adapter_result(
        result: SourceAdapterResult,
        *,
        payload: bytes,
        adapter_name: str,
        adapter_version: str,
        media_types: tuple[str, ...],
    ) -> None:
        if result.protocol_version != SOURCE_ADAPTER_PROTOCOL_VERSION:
            raise AdapterError(f"unsupported source adapter protocol_version: {result.protocol_version!r} != {SOURCE_ADAPTER_PROTOCOL_VERSION!r}")
        if result.adapter_name != adapter_name or result.adapter_version != adapter_version:
            raise AdapterError("source adapter result identity disagrees with active adapter")
        if result.media_type not in media_types:
            raise AdapterError(f"source adapter returned undeclared media_type {result.media_type!r}")
        expected_hash = SourceAdapterResult.hash_bytes(payload)
        if result.content_hash != expected_hash:
            raise AdapterError("source adapter content_hash does not match exact input bytes")
