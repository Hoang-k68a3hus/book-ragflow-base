from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, model_validator

from rag.source_understanding.assembly import CanonicalDocumentAssembler
from rag.source_understanding.atomic.normalizer import ElementNormalizationResult, ElementNormalizer
from rag.source_understanding.completion import (
    UnderstandingCompletionBuilder,
    UnderstandingCompletionReport,
)
from rag.source_understanding.pipeline_support import (
    SourceUnderstandingPipelineError,
    diagnostic_message,
    processing_with_normalizer_manifest,
    processing_with_pipeline_manifest,
    run_stage,
    validate_processing_manifest,
    validate_semantic_boundary,
    validate_source_identity,
    validate_stage_counts,
)
from rag.source_understanding.profiling.content_profiler import ContentProfile, ContentProfiler
from rag.source_understanding.profiling.order_quality import OrderQualityEstimator, OrderQualityReport
from rag.source_understanding.profiling.regions import (
    ContentRegionSegmentationResult,
    ContentRegionSegmenter,
)
from rag.source_understanding.profiling.text_quality import TextQualityEstimator, TextQualityReport
from rag.source_understanding.relations.builder import RelationBuildResult, StructuralRelationBuilder
from rag.source_understanding.schemas.context import Identifier, SchemaModel
from rag.source_understanding.schemas.document import (
    Asset,
    CanonicalDocument,
    ContentRegion,
    DocumentMetadata,
    DocumentQuality,
    ProcessingManifest,
)
from rag.source_understanding.schemas.element import Element, RawElement
from rag.source_understanding.schemas.relation import Relation
from rag.source_understanding.semantics.annotator import SemanticAnnotationResult, SemanticAnnotator
from rag.source_understanding.structure.boundary import BoundaryScorer, BoundarySet
from rag.source_understanding.structure.grouping import GroupingResult, LogicalGroupBuilder
from rag.source_understanding.structure.hierarchy import HierarchyBuilder, HierarchyResult
from rag.source_understanding.structure.integration import ContextIntegrationResult, ContextIntegrator
from rag.source_understanding.structure.integrity import (
    IntegrityConsolidationReport,
    IntegrityGroupConsolidator,
)
from rag.source_understanding.structure.quality import (
    StructureQualityEstimator,
    StructureQualityReport,
)
from rag.source_understanding.structure.region_routing import RegionRouter
from rag.source_understanding.structure.signals import StructureSignalExtractor, StructureSignalSet

SOURCE_UNDERSTANDING_PIPELINE_VERSION = "6"


class SemanticFailureMode(StrEnum):
    KEEP_STRUCTURAL = "KEEP_STRUCTURAL"
    RAISE = "RAISE"


class SemanticStageStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    COMPLETED = "COMPLETED"
    FAILED_OPTIONAL = "FAILED_OPTIONAL"


class SourceUnderstandingPipelinePolicy(SchemaModel):
    version: str = Field(
        default=SOURCE_UNDERSTANDING_PIPELINE_VERSION,
        min_length=1,
        max_length=128,
    )
    semantic_failure_mode: SemanticFailureMode = SemanticFailureMode.KEEP_STRUCTURAL
    auto_segment_regions: bool = True


class SourceUnderstandingResult(SchemaModel):
    version: str = SOURCE_UNDERSTANDING_PIPELINE_VERSION
    document_id: Identifier
    content_profile: ContentProfile
    signal_set: StructureSignalSet
    boundary_set: BoundarySet
    grouping_result: GroupingResult
    integrity_report: IntegrityConsolidationReport
    hierarchy_result: HierarchyResult
    region_result: ContentRegionSegmentationResult | None = None
    integration_result: ContextIntegrationResult
    relation_result: RelationBuildResult
    quality_report: StructureQualityReport
    text_quality_report: TextQualityReport
    order_quality_report: OrderQualityReport
    normalization_result: ElementNormalizationResult | None = None
    structural_document: CanonicalDocument
    document: CanonicalDocument
    semantic_status: SemanticStageStatus
    semantic_result: SemanticAnnotationResult | None = None
    semantic_error: str | None = Field(default=None, max_length=8192)
    completion_report: UnderstandingCompletionReport

    @model_validator(mode="after")
    def validate_result(self) -> SourceUnderstandingResult:
        if self.normalization_result is not None:
            if self.normalization_result.document_id != self.document_id:
                raise ValueError("normalization_result document_id does not match pipeline result")
            if self.normalization_result.elements != self.structural_document.elements:
                raise ValueError("normalization_result elements do not match structural document")
        if self.structural_document.document_id != self.document_id:
            raise ValueError("structural_document document_id does not match pipeline result")
        if self.document.document_id != self.document_id:
            raise ValueError("final document_id does not match pipeline result")
        if self.document.content_hash != self.structural_document.content_hash:
            raise ValueError("semantic stage cannot change canonical content_hash")
        if self.document.source_revision != self.structural_document.source_revision:
            raise ValueError("semantic stage cannot change canonical source_revision")
        if self.semantic_status == SemanticStageStatus.COMPLETED:
            if self.semantic_result is None or self.semantic_error is not None:
                raise ValueError("completed semantic stage requires result and no error")
        elif self.semantic_status == SemanticStageStatus.FAILED_OPTIONAL:
            if self.semantic_result is not None or not self.semantic_error:
                raise ValueError("failed optional semantic stage requires error and no result")
            if self.document != self.structural_document:
                raise ValueError("failed optional semantic stage must keep structural document")
        elif self.semantic_status == SemanticStageStatus.NOT_CONFIGURED:
            if self.semantic_result is not None or self.semantic_error is not None:
                raise ValueError("unconfigured semantic stage cannot carry result/error")
            if self.document != self.structural_document:
                raise ValueError("unconfigured semantic stage must keep structural document")
        if self.completion_report.document_id != self.document_id:
            raise ValueError("completion_report document_id does not match pipeline result")
        return self


class SourceUnderstandingPipeline:
    """Orchestrate format-agnostic structural understanding with optional semantics."""

    version = SOURCE_UNDERSTANDING_PIPELINE_VERSION

    def __init__(
        self,
        *,
        normalizer: ElementNormalizer | None = None,
        profiler: ContentProfiler | None = None,
        signal_extractor: StructureSignalExtractor | None = None,
        boundary_scorer: BoundaryScorer | None = None,
        group_builder: LogicalGroupBuilder | None = None,
        integrity_consolidator: IntegrityGroupConsolidator | None = None,
        hierarchy_builder: HierarchyBuilder | None = None,
        region_segmenter: ContentRegionSegmenter | None = None,
        region_router: RegionRouter | None = None,
        context_integrator: ContextIntegrator | None = None,
        relation_builder: StructuralRelationBuilder | None = None,
        quality_estimator: StructureQualityEstimator | None = None,
        text_quality_estimator: TextQualityEstimator | None = None,
        order_quality_estimator: OrderQualityEstimator | None = None,
        assembler: CanonicalDocumentAssembler | None = None,
        semantic_annotator: SemanticAnnotator | None = None,
        completion_builder: UnderstandingCompletionBuilder | None = None,
        policy: SourceUnderstandingPipelinePolicy | None = None,
    ) -> None:
        self._normalizer = normalizer if normalizer is not None else ElementNormalizer()
        self._profiler = profiler if profiler is not None else ContentProfiler()
        self._signal_extractor = signal_extractor if signal_extractor is not None else StructureSignalExtractor()
        self._boundary_scorer = boundary_scorer if boundary_scorer is not None else BoundaryScorer()
        self._group_builder = group_builder if group_builder is not None else LogicalGroupBuilder()
        self._integrity_consolidator = integrity_consolidator if integrity_consolidator is not None else IntegrityGroupConsolidator()
        self._hierarchy_builder = hierarchy_builder if hierarchy_builder is not None else HierarchyBuilder()
        self._region_segmenter = region_segmenter if region_segmenter is not None else ContentRegionSegmenter()
        self._region_router = region_router if region_router is not None else RegionRouter()
        self._context_integrator = context_integrator if context_integrator is not None else ContextIntegrator()
        self._relation_builder = relation_builder if relation_builder is not None else StructuralRelationBuilder()
        self._quality_estimator = quality_estimator if quality_estimator is not None else StructureQualityEstimator()
        self._text_quality_estimator = text_quality_estimator if text_quality_estimator is not None else TextQualityEstimator()
        self._order_quality_estimator = order_quality_estimator if order_quality_estimator is not None else OrderQualityEstimator()
        self._assembler = assembler if assembler is not None else CanonicalDocumentAssembler()
        self._semantic_annotator = semantic_annotator
        self._completion_builder = completion_builder if completion_builder is not None else UnderstandingCompletionBuilder()
        self._policy = policy if policy is not None else SourceUnderstandingPipelinePolicy()

    def understand_raw(
        self,
        *,
        document_id: str,
        content_hash: str,
        processing: ProcessingManifest,
        raw_elements: Sequence[RawElement],
        source_revision: str | None = None,
        metadata: DocumentMetadata | None = None,
        base_quality: DocumentQuality | None = None,
        regions: Sequence[ContentRegion] = (),
        assets: Sequence[Asset] = (),
        additional_relations: Sequence[Relation] = (),
        adapter_diagnostics: Sequence[object] = (),
    ) -> SourceUnderstandingResult:
        validate_source_identity(document_id, content_hash)
        validate_processing_manifest(processing)
        normalization_result = run_stage(
            "element normalization",
            lambda: self._normalizer.normalize(
                tuple(raw_elements),
                document_id=document_id,
            ),
        )
        processing = processing_with_normalizer_manifest(
            processing,
            normalization_result,
            self._normalizer,
        )
        return self._understand_elements(
            document_id=document_id,
            content_hash=content_hash,
            processing=processing,
            elements=normalization_result.elements,
            normalization_result=normalization_result,
            source_revision=source_revision,
            metadata=metadata,
            base_quality=base_quality,
            regions=regions,
            assets=assets,
            additional_relations=additional_relations,
            adapter_diagnostics=adapter_diagnostics,
        )

    def understand(
        self,
        *,
        document_id: str,
        content_hash: str,
        processing: ProcessingManifest,
        elements: Sequence[Element],
        source_revision: str | None = None,
        metadata: DocumentMetadata | None = None,
        base_quality: DocumentQuality | None = None,
        regions: Sequence[ContentRegion] = (),
        assets: Sequence[Asset] = (),
        additional_relations: Sequence[Relation] = (),
    ) -> SourceUnderstandingResult:
        validate_source_identity(document_id, content_hash)
        validate_processing_manifest(processing)
        return self._understand_elements(
            document_id=document_id,
            content_hash=content_hash,
            processing=processing,
            elements=elements,
            normalization_result=None,
            source_revision=source_revision,
            metadata=metadata,
            base_quality=base_quality,
            regions=regions,
            assets=assets,
            additional_relations=additional_relations,
            adapter_diagnostics=(),
        )

    def _understand_elements(
        self,
        *,
        document_id: str,
        content_hash: str,
        processing: ProcessingManifest,
        elements: Sequence[Element],
        normalization_result: ElementNormalizationResult | None,
        source_revision: str | None,
        metadata: DocumentMetadata | None,
        base_quality: DocumentQuality | None,
        regions: Sequence[ContentRegion],
        assets: Sequence[Asset],
        additional_relations: Sequence[Relation],
        adapter_diagnostics: Sequence[object],
    ) -> SourceUnderstandingResult:
        element_snapshot = tuple(elements)
        region_snapshot = tuple(regions)
        asset_snapshot = tuple(assets)
        extra_relations = tuple(additional_relations)
        adapter_diagnostic_snapshot = tuple(adapter_diagnostics)

        text_quality_report = run_stage(
            "text quality estimation",
            lambda: self._text_quality_estimator.estimate(
                element_snapshot,
                language_hint=(metadata.language if metadata is not None else None),
            ),
        )
        base_quality = self._merge_detected_quality(base_quality, text_quality_report.quality)
        order_quality_report = run_stage(
            "order quality estimation",
            lambda: self._order_quality_estimator.estimate(element_snapshot),
        )
        base_quality = self._merge_detected_quality(base_quality, order_quality_report.quality)

        content_profile = run_stage(
            "content profiling",
            lambda: self._profiler.analyze(element_snapshot),
        )
        signal_set = run_stage(
            "structure signal extraction",
            lambda: self._signal_extractor.extract(element_snapshot),
        )
        boundary_set = run_stage(
            "boundary scoring",
            lambda: self._boundary_scorer.score(element_snapshot, signal_set),
        )
        grouping_result = run_stage(
            "logical grouping",
            lambda: self._group_builder.build(
                element_snapshot,
                signal_set,
                boundary_set,
            ),
        )
        grouping_result, integrity_report = run_stage(
            "integrity consolidation",
            lambda: self._integrity_consolidator.consolidate(
                element_snapshot,
                boundary_set,
                grouping_result,
            ),
        )
        hierarchy_result = run_stage(
            "hierarchy understanding",
            lambda: self._hierarchy_builder.build(
                element_snapshot,
                signal_set,
                boundary_set,
            ),
        )

        region_result: ContentRegionSegmentationResult | None = None
        region_source = "NONE"
        if region_snapshot:
            region_source = "CALLER"
            grouping_result = run_stage(
                "caller region routing",
                lambda: self._region_router.assign_grouping(
                    grouping_result,
                    region_snapshot,
                ),
            )
        elif self._policy.auto_segment_regions:
            region_result = run_stage(
                "content region segmentation",
                lambda: self._region_segmenter.segment(
                    element_snapshot,
                    hierarchy_result,
                    grouping_result,
                ),
            )
            grouping_result, hierarchy_result = run_stage(
                "content region routing",
                lambda: self._region_router.apply(
                    grouping_result,
                    hierarchy_result,
                    region_result,
                ),
            )
            region_snapshot = region_result.regions
            region_source = "AUTO"

        integration_result = run_stage(
            "context integration",
            lambda: self._context_integrator.integrate(
                grouping_result,
                hierarchy_result,
            ),
        )
        relation_result = run_stage(
            "structural relation building",
            lambda: self._relation_builder.build(
                element_snapshot,
                integration_result.logical_units,
                grouping_result.subdocuments,
            ),
        )
        quality_report = run_stage(
            "structure quality estimation",
            lambda: self._quality_estimator.estimate(
                element_snapshot,
                boundary_set,
                grouping_result,
                hierarchy_result,
            ),
        )
        validate_stage_counts(
            len(element_snapshot),
            content_profile,
            signal_set,
            boundary_set,
            grouping_result,
            hierarchy_result,
            integration_result,
            relation_result,
            quality_report,
            text_quality_report,
            order_quality_report,
            integrity_report,
            region_result,
        )
        processing = processing_with_pipeline_manifest(
            processing,
            pipeline_version=self.version,
            pipeline_policy=self._policy,
            content_profile=content_profile,
            signal_set=signal_set,
            boundary_set=boundary_set,
            grouping_result=grouping_result,
            hierarchy_result=hierarchy_result,
            integration_result=integration_result,
            relation_result=relation_result,
            quality_report=quality_report,
            text_quality_report=text_quality_report,
            order_quality_report=order_quality_report,
            integrity_report=integrity_report,
            region_result=region_result,
            region_source=region_source,
            region_count=len(region_snapshot),
            region_router=self._region_router,
            completion_builder=self._completion_builder,
            assembler=self._assembler,
        )
        structural_document = run_stage(
            "canonical assembly",
            lambda: self._assembler.assemble(
                document_id=document_id,
                content_hash=content_hash,
                source_revision=source_revision,
                processing=processing,
                metadata=metadata,
                elements=element_snapshot,
                grouping_result=grouping_result,
                hierarchy_result=hierarchy_result,
                integration_result=integration_result,
                relation_result=relation_result,
                quality_report=quality_report,
                base_quality=base_quality,
                regions=region_snapshot,
                assets=asset_snapshot,
                additional_relations=extra_relations,
            ),
        )

        final_document = structural_document
        semantic_status = SemanticStageStatus.NOT_CONFIGURED
        semantic_result: SemanticAnnotationResult | None = None
        semantic_error: str | None = None
        if self._semantic_annotator is not None:
            try:
                semantic_result = self._semantic_annotator.annotate(structural_document)
                validate_semantic_boundary(
                    structural_document,
                    semantic_result.document,
                )
            except Exception as exc:
                if self._policy.semantic_failure_mode == SemanticFailureMode.RAISE:
                    raise SourceUnderstandingPipelineError(f"semantic enrichment failed: {exc}") from exc
                semantic_status = SemanticStageStatus.FAILED_OPTIONAL
                semantic_error = diagnostic_message(exc)
                semantic_result = None
            else:
                semantic_status = SemanticStageStatus.COMPLETED
                final_document = semantic_result.document

        completion_report = run_stage(
            "understanding completion reporting",
            lambda: self._completion_builder.build(
                document=final_document,
                boundary_set=boundary_set,
                grouping_result=grouping_result,
                hierarchy_result=hierarchy_result,
                integrity_report=integrity_report,
                quality_report=quality_report,
                region_result=region_result,
                semantic_status=semantic_status.value,
                semantic_result=semantic_result,
                adapter_diagnostics=adapter_diagnostic_snapshot,
            ),
        )
        return SourceUnderstandingResult(
            document_id=document_id,
            content_profile=content_profile,
            signal_set=signal_set,
            boundary_set=boundary_set,
            grouping_result=grouping_result,
            integrity_report=integrity_report,
            hierarchy_result=hierarchy_result,
            region_result=region_result,
            integration_result=integration_result,
            relation_result=relation_result,
            quality_report=quality_report,
            text_quality_report=text_quality_report,
            order_quality_report=order_quality_report,
            normalization_result=normalization_result,
            structural_document=structural_document,
            document=final_document,
            semantic_status=semantic_status,
            semantic_result=semantic_result,
            semantic_error=semantic_error,
            completion_report=completion_report,
        )

    @staticmethod
    def _merge_detected_quality(
        supplied: DocumentQuality | None,
        detected: DocumentQuality,
    ) -> DocumentQuality:
        if supplied is None:
            return detected
        data = detected.model_dump(mode="python")
        supplied_data = supplied.model_dump(mode="python")
        for field in (
            "text_quality",
            "order_quality",
            "duplicate_ratio",
            "garbage_ratio",
            "overall",
            "structure_quality",
            "lexical",
        ):
            if supplied_data[field] is not None:
                data[field] = supplied_data[field]
        data["warnings"] = tuple(dict.fromkeys((*detected.warnings, *supplied.warnings)))
        data["metrics"] = {**detected.metrics, **supplied.metrics}
        return DocumentQuality.model_validate(data)


__all__ = [
    "SOURCE_UNDERSTANDING_PIPELINE_VERSION",
    "SemanticFailureMode",
    "SemanticStageStatus",
    "SourceUnderstandingPipeline",
    "SourceUnderstandingPipelineError",
    "SourceUnderstandingPipelinePolicy",
    "SourceUnderstandingResult",
]
