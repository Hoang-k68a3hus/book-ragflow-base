from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from rag.source_understanding.atomic.normalizer import ElementNormalizationResult
from rag.source_understanding.profiling.content_profiler import ContentProfile
from rag.source_understanding.profiling.order_quality import OrderQualityReport
from rag.source_understanding.profiling.regions import ContentRegionSegmentationResult
from rag.source_understanding.profiling.text_quality import TextQualityReport
from rag.source_understanding.relations.builder import RelationBuildResult
from rag.source_understanding.schemas.document import CanonicalDocument, ProcessingManifest
from rag.source_understanding.source_attributes import SOURCE_ATTRIBUTE_CONTRACT_VERSION
from rag.source_understanding.structure.boundary import BoundarySet
from rag.source_understanding.structure.grouping import GroupingResult
from rag.source_understanding.structure.hierarchy import HierarchyResult
from rag.source_understanding.structure.integration import ContextIntegrationResult
from rag.source_understanding.structure.integrity import IntegrityConsolidationReport
from rag.source_understanding.structure.quality import StructureQualityReport
from rag.source_understanding.structure.signals import StructureSignalSet

T = TypeVar("T")


class SourceUnderstandingPipelineError(ValueError):
    """One source-understanding stage failed or violated a stage boundary."""


def processing_with_normalizer_manifest(
    processing: ProcessingManifest,
    normalization_result: ElementNormalizationResult,
    normalizer: object,
) -> ProcessingManifest:
    normalizer_version = getattr(normalizer, "version", None)
    if not isinstance(normalizer_version, str) or not normalizer_version:
        raise SourceUnderstandingPipelineError("element normalizer must expose a non-blank version")
    if processing.normalizer_version is not None and processing.normalizer_version != normalizer_version:
        raise SourceUnderstandingPipelineError(f"processing.normalizer_version conflicts with active normalizer: {processing.normalizer_version!r} != {normalizer_version!r}")
    configuration = dict(processing.configuration)
    configuration["element_normalization"] = {
        "normalizer_version": normalizer_version,
        "policy": normalization_result.policy.model_dump(mode="json"),
    }
    data = processing.model_dump(mode="python")
    data["normalizer_version"] = normalizer_version
    data["configuration"] = configuration
    return ProcessingManifest.model_validate(data)


def processing_with_pipeline_manifest(
    processing: ProcessingManifest,
    *,
    pipeline_version: str,
    pipeline_policy: BaseModel,
    content_profile: ContentProfile,
    signal_set: StructureSignalSet,
    boundary_set: BoundarySet,
    grouping_result: GroupingResult,
    hierarchy_result: HierarchyResult,
    integration_result: ContextIntegrationResult,
    relation_result: RelationBuildResult,
    quality_report: StructureQualityReport,
    text_quality_report: TextQualityReport,
    order_quality_report: OrderQualityReport,
    integrity_report: IntegrityConsolidationReport,
    region_result: ContentRegionSegmentationResult | None,
    region_source: str,
    region_count: int,
    region_router: object,
    completion_builder: object,
    assembler: object,
) -> ProcessingManifest:
    suppression_reason_counts = Counter(hierarchy_result.suppression_reasons.values())
    configuration = dict(processing.configuration)
    configuration["source_understanding_pipeline"] = {
        "pipeline_version": pipeline_version,
        "policy": pipeline_policy.model_dump(mode="json"),
        "source_attribute_contract_version": SOURCE_ATTRIBUTE_CONTRACT_VERSION,
        "content_profiler_version": content_profile.version,
        "content_regions": {
            "source": region_source,
            "count": region_count,
            "segmenter_version": (region_result.version if region_result is not None else None),
            "segmenter_policy": (region_result.policy.model_dump(mode="json") if region_result is not None else None),
            "routing_version": (getattr(region_router, "version", None) if region_count > 0 else None),
        },
        "structure_signal_version": signal_set.version,
        "structure_signal_policy": signal_set.policy.model_dump(mode="json"),
        "boundary_version": boundary_set.version,
        "boundary_policy": boundary_set.policy.model_dump(mode="json"),
        "grouping_version": grouping_result.version,
        "grouping_policy": grouping_result.policy.model_dump(mode="json"),
        "integrity_consolidation": {
            "version": integrity_report.version,
            "policy": integrity_report.policy.model_dump(mode="json"),
            "consolidated_unit_count": len(integrity_report.consolidated_unit_ids),
            "family_counts": dict(integrity_report.family_counts),
            "replaced_unit_count": len(integrity_report.replaced_unit_ids),
        },
        "hierarchy_version": hierarchy_result.version,
        "hierarchy_policy": hierarchy_result.policy.model_dump(mode="json"),
        "hierarchy_suppression_diagnostics": {
            "suppressed_count": len(hierarchy_result.suppressed_context_element_ids),
            "reason_counts": dict(sorted(suppression_reason_counts.items())),
            "items": [
                {"element_id": element_id, "reason": hierarchy_result.suppression_reasons[element_id]}
                for element_id in hierarchy_result.suppressed_context_element_ids
            ],
        },
        "context_integration_version": integration_result.version,
        "relation_builder_version": relation_result.version,
        "relation_policy": relation_result.policy.model_dump(mode="json"),
        "structure_quality_version": quality_report.version,
        "structure_quality_policy": quality_report.policy.model_dump(mode="json"),
        "text_quality_version": text_quality_report.version,
        "text_quality_policy": text_quality_report.policy.model_dump(mode="json"),
        "lexical_quality_version": text_quality_report.lexical_quality.version,
        "lexical_quality_policy": text_quality_report.lexical_quality.policy.model_dump(mode="json"),
        "order_quality_version": order_quality_report.version,
        "order_quality_policy": order_quality_report.policy.model_dump(mode="json"),
        "completion_report_version": getattr(completion_builder, "version", "unknown"),
        "assembly_version": getattr(assembler, "version", "unknown"),
    }
    data = processing.model_dump(mode="python")
    data["configuration"] = configuration
    return ProcessingManifest.model_validate(data)


def validate_stage_counts(
    expected: int,
    content_profile: ContentProfile,
    signal_set: StructureSignalSet,
    boundary_set: BoundarySet,
    grouping_result: GroupingResult,
    hierarchy_result: HierarchyResult,
    integration_result: ContextIntegrationResult,
    relation_result: RelationBuildResult,
    quality_report: StructureQualityReport,
    text_quality_report: TextQualityReport,
    order_quality_report: OrderQualityReport,
    integrity_report: IntegrityConsolidationReport,
    region_result: ContentRegionSegmentationResult | None,
) -> None:
    counts = {
        "content_profile": content_profile.element_count,
        "signals": signal_set.element_count,
        "boundaries": boundary_set.element_count,
        "grouping": grouping_result.element_count,
        "hierarchy": hierarchy_result.element_count,
        "integration": integration_result.element_count,
        "relations": relation_result.element_count,
        "quality": quality_report.metrics.element_count,
        "text_quality": text_quality_report.metrics.element_count,
        "order_quality": order_quality_report.metrics.element_count,
        "integrity": integrity_report.element_count,
    }
    if region_result is not None:
        counts["regions"] = region_result.element_count
    mismatched = {name: count for name, count in counts.items() if count != expected}
    if mismatched:
        raise SourceUnderstandingPipelineError(f"source-understanding stage element_count mismatch: expected {expected}, got {mismatched}")


def validate_semantic_boundary(
    structural: CanonicalDocument,
    enriched: CanonicalDocument,
) -> None:
    immutable_fields = (
        "schema_version",
        "document_id",
        "content_hash",
        "source_revision",
        "metadata",
        "structure",
        "elements",
        "regions",
        "logical_units",
        "context_nodes",
        "relations",
        "assets",
        "subdocuments",
        "quality",
    )
    changed = [name for name in immutable_fields if getattr(structural, name) != getattr(enriched, name)]
    if changed:
        raise SourceUnderstandingPipelineError(f"semantic stage mutated canonical source/structure fields: {changed}")
    processing_fields = (
        "adapter_name",
        "adapter_version",
        "normalizer_version",
        "structure_version",
        "processed_at",
    )
    changed_processing = [name for name in processing_fields if getattr(structural.processing, name) != getattr(enriched.processing, name)]
    if changed_processing:
        raise SourceUnderstandingPipelineError(f"semantic stage mutated non-semantic processing identity: {changed_processing}")
    structural_config = dict(structural.processing.configuration)
    enriched_config = dict(enriched.processing.configuration)
    enriched_config.pop("semantic_understanding", None)
    if structural_config != enriched_config:
        raise SourceUnderstandingPipelineError("semantic stage mutated processing configuration outside its namespace")


def validate_processing_manifest(processing: ProcessingManifest) -> None:
    processed_at = processing.processed_at
    if processed_at.tzinfo is None or processed_at.utcoffset() is None:
        raise SourceUnderstandingPipelineError("processing.processed_at must be timezone-aware")


def validate_source_identity(document_id: str, content_hash: str) -> None:
    if not isinstance(document_id, str) or not document_id or document_id.strip() != document_id:
        raise SourceUnderstandingPipelineError("document_id must be a trimmed non-blank string")
    if not isinstance(content_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash):
        raise SourceUnderstandingPipelineError("content_hash must be canonical lowercase sha256:<64 hex>")


def diagnostic_message(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:8192]


def run_stage[T](name: str, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except SourceUnderstandingPipelineError:
        raise
    except Exception as exc:
        raise SourceUnderstandingPipelineError(f"{name} failed: {exc}") from exc
