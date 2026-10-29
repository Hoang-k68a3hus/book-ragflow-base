from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from rag.source_understanding.relations.builder import RelationBuildResult
from rag.source_understanding.schemas.document import (
    Asset,
    CanonicalDocument,
    ContentRegion,
    DocumentMetadata,
    DocumentQuality,
    ProcessingManifest,
)
from rag.source_understanding.schemas.element import Element
from rag.source_understanding.schemas.relation import Relation, RelationLayer
from rag.source_understanding.structure.grouping import GroupingResult
from rag.source_understanding.structure.hierarchy import HierarchyResult
from rag.source_understanding.structure.integration import ContextIntegrationResult
from rag.source_understanding.structure.quality import StructureQualityReport

ASSEMBLY_VERSION = "4"
STRUCTURE_PIPELINE_VERSION = "6"


class AssemblyError(ValueError):
    """Structural stage outputs cannot be assembled into a canonical document safely."""


class CanonicalDocumentAssembler:
    """Materialize validated structural outputs into one CanonicalDocument."""

    version: str = ASSEMBLY_VERSION
    structure_pipeline_version: str = STRUCTURE_PIPELINE_VERSION

    def assemble(
        self,
        *,
        document_id: str,
        content_hash: str,
        processing: ProcessingManifest,
        elements: Sequence[Element],
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
        integration_result: ContextIntegrationResult,
        relation_result: RelationBuildResult,
        quality_report: StructureQualityReport,
        source_revision: str | None = None,
        metadata: DocumentMetadata | None = None,
        base_quality: DocumentQuality | None = None,
        regions: Sequence[ContentRegion] = (),
        assets: Sequence[Asset] = (),
        additional_relations: Sequence[Relation] = (),
    ) -> CanonicalDocument:
        element_snapshot = tuple(elements)
        region_snapshot = tuple(regions)
        asset_snapshot = tuple(assets)
        extra_relation_snapshot = tuple(additional_relations)

        self._validate_stage_alignment(
            element_snapshot,
            grouping_result,
            hierarchy_result,
            integration_result,
            relation_result,
            quality_report,
        )
        self._validate_additional_relations(
            relation_result.relations,
            extra_relation_snapshot,
        )

        processing_with_structure = self._processing_with_structure_version(processing)
        quality = self._merge_quality(base_quality, quality_report.quality)
        relations = (*relation_result.relations, *extra_relation_snapshot)

        try:
            return CanonicalDocument(
                document_id=document_id,
                content_hash=content_hash,
                source_revision=source_revision,
                processing=processing_with_structure,
                metadata=metadata if metadata is not None else DocumentMetadata(),
                structure=hierarchy_result.structure,
                elements=element_snapshot,
                regions=region_snapshot,
                logical_units=integration_result.logical_units,
                context_nodes=hierarchy_result.context_nodes,
                relations=relations,
                assets=asset_snapshot,
                subdocuments=grouping_result.subdocuments,
                quality=quality,
            )
        except ValidationError as exc:
            raise AssemblyError(f"canonical document validation failed after structural assembly: {exc}") from exc

    def _processing_with_structure_version(
        self,
        processing: ProcessingManifest,
    ) -> ProcessingManifest:
        if processing.structure_version is not None and processing.structure_version != self.structure_pipeline_version:
            raise AssemblyError(f"processing.structure_version conflicts with the structural pipeline version: {processing.structure_version!r} != {self.structure_pipeline_version!r}")

        data = processing.model_dump(mode="python")
        data["structure_version"] = self.structure_pipeline_version
        return ProcessingManifest.model_validate(data)

    @staticmethod
    def _merge_quality(
        base_quality: DocumentQuality | None,
        structure_quality: DocumentQuality,
    ) -> DocumentQuality:
        if base_quality is None:
            return structure_quality

        if base_quality.structure_quality is not None and base_quality.structure_quality != structure_quality.structure_quality:
            raise AssemblyError("base quality already carries a conflicting structure_quality value")

        metrics = dict(base_quality.metrics)
        for key, value in structure_quality.metrics.items():
            if key in metrics and metrics[key] != value:
                raise AssemblyError(f"quality metric {key!r} conflicts between base and structure stages")
            metrics[key] = value

        warnings = tuple(dict.fromkeys((*base_quality.warnings, *structure_quality.warnings)))
        data = base_quality.model_dump(mode="python")
        data["structure_quality"] = structure_quality.structure_quality
        dimension_scores = [
            score
            for score in (
                data.get("text_quality"),
                data.get("order_quality"),
                data.get("structure_quality"),
            )
            if score is not None
        ]
        if dimension_scores:
            data["overall"] = 0.0 if any(score == 0 for score in dimension_scores) else len(dimension_scores) / sum(1 / score for score in dimension_scores)
            metrics["overall_quality_method"] = "harmonic_mean_available_dimensions"
            metrics["overall_quality_dimensions"] = [
                name
                for name in ("text_quality", "order_quality", "structure_quality")
                if data.get(name) is not None
            ]
        data["warnings"] = warnings
        data["metrics"] = metrics
        return DocumentQuality.model_validate(data)

    @staticmethod
    def _validate_additional_relations(
        built_relations: tuple[Relation, ...],
        additional_relations: tuple[Relation, ...],
    ) -> None:
        if any(relation.layer != RelationLayer.STRUCTURAL for relation in additional_relations):
            raise AssemblyError("pre-semantic assembly accepts only additional STRUCTURAL relations")

        relations = (*built_relations, *additional_relations)
        relation_ids = [relation.id for relation in relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise AssemblyError("assembled relations contain duplicate ids")

        triples = [(relation.type, relation.source_id, relation.target_id) for relation in relations]
        if len(triples) != len(set(triples)):
            raise AssemblyError("assembled relations contain duplicate relation triples")

    @staticmethod
    def _validate_stage_alignment(
        elements: tuple[Element, ...],
        grouping_result: GroupingResult,
        hierarchy_result: HierarchyResult,
        integration_result: ContextIntegrationResult,
        relation_result: RelationBuildResult,
        quality_report: StructureQualityReport,
    ) -> None:
        if not elements:
            raise AssemblyError("cannot assemble an empty canonical source")

        element_ids = [element.id for element in elements]
        if len(element_ids) != len(set(element_ids)):
            raise AssemblyError("assembly elements must have unique ids")
        orders = [element.order for element in elements]
        if len(orders) != len(set(orders)):
            raise AssemblyError("assembly elements must have unique order values")
        if orders != sorted(orders):
            raise AssemblyError("assembly elements must follow ascending canonical source order")

        expected = len(elements)
        stage_counts = {
            "grouping": grouping_result.element_count,
            "hierarchy": hierarchy_result.element_count,
            "integration": integration_result.element_count,
            "relations": relation_result.element_count,
            "quality": quality_report.metrics.element_count,
        }
        mismatched = {stage: count for stage, count in stage_counts.items() if count != expected}
        if mismatched:
            raise AssemblyError(f"structural stage element_count mismatch: expected {expected}, got {mismatched}")

        if grouping_result.signal_version != hierarchy_result.signal_version:
            raise AssemblyError("grouping and hierarchy were built from different signal versions")
        if grouping_result.boundary_version != hierarchy_result.boundary_version:
            raise AssemblyError("grouping and hierarchy were built from different boundary versions")
        if integration_result.grouping_version != grouping_result.version:
            raise AssemblyError("context integration does not reference the current grouping version")
        if integration_result.hierarchy_version != hierarchy_result.version:
            raise AssemblyError("context integration does not reference the current hierarchy version")

        assignment_ids = [assignment.element_id for assignment in hierarchy_result.assignments]
        if assignment_ids != element_ids:
            raise AssemblyError("hierarchy assignments must follow the exact canonical element order")

        base_units = grouping_result.logical_units
        integrated_units = integration_result.logical_units
        if len(base_units) != len(integrated_units):
            raise AssemblyError("context integration changed the logical-unit cardinality")
        for base, integrated in zip(base_units, integrated_units):
            base_data = base.model_dump(mode="python")
            integrated_data = integrated.model_dump(mode="python")
            base_data.pop("context_node_ids", None)
            integrated_data.pop("context_node_ids", None)
            if base_data != integrated_data:
                raise AssemblyError(f"context integration changed non-context fields for logical unit {base.id!r}")

        if relation_result.logical_unit_count != len(integrated_units):
            raise AssemblyError("relation result logical_unit_count does not match integrated units")
        if relation_result.subdocument_count != len(grouping_result.subdocuments):
            raise AssemblyError("relation result subdocument_count does not match grouping result")

        if quality_report.metrics.logical_unit_count != len(base_units):
            raise AssemblyError("structure quality logical_unit_count does not match grouping result")
        if quality_report.metrics.subdocument_count != len(grouping_result.subdocuments):
            raise AssemblyError("structure quality subdocument_count does not match grouping result")

        reported_mode = quality_report.quality.metrics.get("structure_mode")
        if reported_mode is not None and reported_mode != hierarchy_result.structure.mode.value:
            raise AssemblyError("structure quality report was computed for a different structure mode")
