from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from .context import (
    Confidence,
    ConfidenceMap,
    ContentHash,
    ContextNode,
    Identifier,
    JsonObject,
    Label,
    SchemaModel,
    StructureMode,
    StructureSource,
)
from .element import Element, SourceLocation
from .logical_unit import LogicalUnit
from .relation import Relation, RelationType


class SemanticAnnotationType(StrEnum):
    TOPIC = "TOPIC"
    CONCEPT = "CONCEPT"
    ENTITY = "ENTITY"
    KEYWORD = "KEYWORD"
    DEFINITION = "DEFINITION"
    EXAMPLE = "EXAMPLE"
    THEOREM = "THEOREM"
    PROOF = "PROOF"
    WARNING = "WARNING"
    NOTE = "NOTE"
    EXERCISE = "EXERCISE"
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    SUMMARY = "SUMMARY"
    PROCEDURE = "PROCEDURE"
    KEY_POINT = "KEY_POINT"
    LEARNING_OBJECTIVE = "LEARNING_OBJECTIVE"
    CUSTOM = "CUSTOM"


class SemanticPayloadMode(StrEnum):
    """How an annotation value may be used downstream.

    ``LABEL_ONLY`` is safe with role/evidence evaluation and never treats the
    provider value as source text. ``EXTRACTIVE`` requires an exact source span.
    ``GENERATIVE`` requires a separate faithfulness contract before its value can
    influence retrieval.
    """

    LABEL_ONLY = "LABEL_ONLY"
    EXTRACTIVE = "EXTRACTIVE"
    GENERATIVE = "GENERATIVE"


_EXTRACTIVE_PAYLOAD_TYPES = frozenset(
    {
        SemanticAnnotationType.CONCEPT,
        SemanticAnnotationType.ENTITY,
        SemanticAnnotationType.KEYWORD,
    }
)
_GENERATIVE_PAYLOAD_TYPES = frozenset(
    {
        SemanticAnnotationType.SUMMARY,
        SemanticAnnotationType.KEY_POINT,
        SemanticAnnotationType.LEARNING_OBJECTIVE,
    }
)


def semantic_payload_mode_for_type(
    annotation_type: SemanticAnnotationType,
) -> SemanticPayloadMode:
    if annotation_type in _EXTRACTIVE_PAYLOAD_TYPES:
        return SemanticPayloadMode.EXTRACTIVE
    if annotation_type in _GENERATIVE_PAYLOAD_TYPES:
        return SemanticPayloadMode.GENERATIVE
    return SemanticPayloadMode.LABEL_ONLY


class SemanticTextView(StrEnum):
    """Element-local text view used by semantic evidence offsets."""

    RAW_TEXT = "RAW_TEXT"
    NORMALIZED_TEXT = "NORMALIZED_TEXT"


class SemanticConfidenceMethod(StrEnum):
    """Meaning of a semantic confidence value.

    A numeric score alone is deliberately not treated as a calibrated
    probability. Retrieval policy can use this provenance to reject scores that
    have not been calibrated or measured on held-out data.
    """

    RULE_PRIOR = "RULE_PRIOR"
    CALIBRATED_PROBABILITY = "CALIBRATED_PROBABILITY"
    EMPIRICAL_PROVIDER_SCORE = "EMPIRICAL_PROVIDER_SCORE"
    UNCALIBRATED = "UNCALIBRATED"


class SemanticEvidenceSpan(SchemaModel):
    """Exact element-local source support for a semantic annotation.

    Offsets are zero-based, start-inclusive/end-exclusive and are evaluated
    against the selected canonical Element text view. They are not document or
    adapter source offsets and must never be promoted to SourceLocation.
    """

    element_id: Identifier
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quoted_text: str = Field(min_length=1, max_length=32768)
    text_view: SemanticTextView = SemanticTextView.RAW_TEXT

    @model_validator(mode="after")
    def validate_span(self) -> SemanticEvidenceSpan:
        if self.end_char <= self.start_char:
            raise ValueError("semantic evidence end_char must be greater than start_char")
        if self.end_char - self.start_char != len(self.quoted_text):
            raise ValueError("semantic evidence range length must equal quoted_text length")
        return self


def semantic_extractive_value_key(value: str) -> str:
    """Normalize an extractive value only for exact source-support validation."""

    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class DocumentMetadata(SchemaModel):
    title: str | None = Field(default=None, max_length=4096)
    language: str | None = Field(default=None, min_length=2, max_length=64)
    source_name: str | None = Field(default=None, max_length=4096)
    authors: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None
    attributes: JsonObject = Field(default_factory=dict)


class ProcessingManifest(SchemaModel):
    """Versions required to reproduce the canonical representation."""

    adapter_name: str = Field(min_length=1, max_length=256)
    adapter_version: str | None = Field(default=None, max_length=128)
    normalizer_version: str | None = Field(default=None, max_length=128)
    structure_version: str | None = Field(default=None, max_length=128)
    semantic_version: str | None = Field(default=None, max_length=128)
    processed_at: datetime
    configuration: JsonObject = Field(default_factory=dict)


class DocumentStructure(SchemaModel):
    mode: StructureMode = StructureMode.UNKNOWN
    source: StructureSource | None = None
    confidence: Confidence | None = None
    signals: ConfidenceMap = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_known_structure_has_provenance(self) -> DocumentStructure:
        if self.mode == StructureMode.UNKNOWN:
            if self.source is not None or self.confidence is not None:
                raise ValueError("UNKNOWN structure must not carry source/confidence claims")
            return self
        if self.source is None or self.confidence is None:
            raise ValueError("known structure mode requires source and confidence")
        return self


class LexicalLanguage(StrEnum):
    VIETNAMESE = "vi"
    ENGLISH = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class LexicalAnomalyReason(StrEnum):
    MULTIPLE_VIETNAMESE_TONES = "MULTIPLE_VIETNAMESE_TONES"
    INVALID_VIETNAMESE_VOWEL_NUCLEUS = "INVALID_VIETNAMESE_VOWEL_NUCLEUS"
    MISPLACED_VIETNAMESE_TONE = "MISPLACED_VIETNAMESE_TONE"
    ENGLISH_UNEXPECTED_DIACRITIC = "ENGLISH_UNEXPECTED_DIACRITIC"
    ENGLISH_CONSONANT_RUN = "ENGLISH_CONSONANT_RUN"
    ENGLISH_REPEATED_LETTER = "ENGLISH_REPEATED_LETTER"


class LexicalAnomalyEvidence(SchemaModel):
    element_id: Identifier
    token: str = Field(min_length=1, max_length=256)
    language: LexicalLanguage
    reason: LexicalAnomalyReason


class LexicalQuality(SchemaModel):
    version: str = Field(min_length=1, max_length=128)
    detected_language: LexicalLanguage
    token_count: int = Field(ge=0)
    vietnamese_token_count: int = Field(ge=0)
    english_token_count: int = Field(ge=0)
    anomaly_count: int = Field(ge=0)
    anomaly_ratio: Confidence
    reason_counts: JsonObject = Field(default_factory=dict)
    element_anomaly_counts: JsonObject = Field(default_factory=dict)
    evidence: tuple[LexicalAnomalyEvidence, ...] = Field(default_factory=tuple)
    evidence_truncated: bool = False

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> LexicalQuality:
        if len(self.evidence) > self.anomaly_count:
            raise ValueError("lexical evidence cannot exceed anomaly_count")
        if self.evidence_truncated != (len(self.evidence) < self.anomaly_count):
            raise ValueError("lexical evidence_truncated must reflect evidence coverage")
        if sum(value for value in self.reason_counts.values() if isinstance(value, int)) != self.anomaly_count:
            raise ValueError("lexical reason_counts must sum to anomaly_count")
        if sum(value for value in self.element_anomaly_counts.values() if isinstance(value, int)) != self.anomaly_count:
            raise ValueError("lexical element_anomaly_counts must sum to anomaly_count")
        return self


class DocumentQuality(SchemaModel):
    text_quality: Confidence | None = None
    order_quality: Confidence | None = None
    structure_quality: Confidence | None = None
    duplicate_ratio: Confidence | None = None
    garbage_ratio: Confidence | None = None
    overall: Confidence | None = None
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    lexical: LexicalQuality | None = None
    metrics: JsonObject = Field(default_factory=dict)


class ContentRegion(SchemaModel):
    """Non-overlapping source region used for local profiling/routing."""

    id: Identifier
    element_ids: tuple[Identifier, ...] = Field(min_length=1)
    dominant_type: Label | None = None
    profile: ConfidenceMap = Field(default_factory=dict)
    structure: DocumentStructure = Field(default_factory=DocumentStructure)
    source: StructureSource
    confidence: Confidence
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_elements(self) -> ContentRegion:
        if len(self.element_ids) != len(set(self.element_ids)):
            raise ValueError("content region element_ids must be unique")
        return self


class Asset(SchemaModel):
    id: Identifier
    type: str = Field(min_length=1, max_length=128)
    uri: str | None = Field(default=None, max_length=8192)
    location: SourceLocation | None = None
    metadata: JsonObject = Field(default_factory=dict)


class SubDocument(SchemaModel):
    id: Identifier
    element_ids: tuple[Identifier, ...] = Field(min_length=1)
    label: str | None = Field(default=None, max_length=2048)
    source_hint: str | None = Field(default=None, max_length=4096)
    confidence: Confidence
    source: StructureSource
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_elements(self) -> SubDocument:
        if len(self.element_ids) != len(set(self.element_ids)):
            raise ValueError("subdocument element_ids must be unique")
        return self


class SemanticAnnotation(SchemaModel):
    id: Identifier
    target_id: Identifier
    type: SemanticAnnotationType
    value: str = Field(min_length=1, max_length=8192)
    payload_mode: SemanticPayloadMode | None = None
    source: StructureSource
    confidence: Confidence
    confidence_method: SemanticConfidenceMethod = SemanticConfidenceMethod.UNCALIBRATED
    calibration_version: str | None = Field(default=None, min_length=1, max_length=128)
    evidence: tuple[SemanticEvidenceSpan, ...] = Field(default_factory=tuple)
    model_version: str | None = Field(default=None, max_length=128)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def default_payload_mode(cls, value: object) -> object:
        if not isinstance(value, dict) or value.get("payload_mode") is not None:
            return value
        annotation_type = value.get("type")
        if annotation_type is None:
            return value
        payload = dict(value)
        payload["payload_mode"] = semantic_payload_mode_for_type(SemanticAnnotationType(annotation_type))
        return payload

    @model_validator(mode="after")
    def validate_semantic_provenance(self) -> SemanticAnnotation:
        if self.confidence_method == SemanticConfidenceMethod.CALIBRATED_PROBABILITY and self.calibration_version is None:
            raise ValueError("CALIBRATED_PROBABILITY semantic confidence requires calibration_version")
        evidence_keys = [
            (
                span.element_id,
                span.text_view,
                span.start_char,
                span.end_char,
                span.quoted_text,
            )
            for span in self.evidence
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("semantic annotation evidence spans must be unique")
        if (
            self.type
            in {
                SemanticAnnotationType.CONCEPT,
                SemanticAnnotationType.ENTITY,
                SemanticAnnotationType.KEYWORD,
            }
            and not self.evidence
        ):
            raise ValueError(f"{self.type.value} semantic annotations require source evidence")
        if self.type in {
            SemanticAnnotationType.CONCEPT,
            SemanticAnnotationType.ENTITY,
            SemanticAnnotationType.KEYWORD,
        } and semantic_extractive_value_key(self.value) not in {semantic_extractive_value_key(item.quoted_text) for item in self.evidence}:
            raise ValueError(f"{self.type.value} semantic annotation value must match an evidence quote")
        return self


class CanonicalDocument(SchemaModel):
    """Loss-minimizing canonical representation of exactly one source revision."""

    schema_version: str = Field(default="1.2", min_length=1, max_length=64)
    document_id: Identifier
    content_hash: ContentHash
    source_revision: Identifier | None = None
    processing: ProcessingManifest
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    structure: DocumentStructure = Field(default_factory=DocumentStructure)
    elements: tuple[Element, ...] = Field(default_factory=tuple)
    regions: tuple[ContentRegion, ...] = Field(default_factory=tuple)
    logical_units: tuple[LogicalUnit, ...] = Field(default_factory=tuple)
    context_nodes: tuple[ContextNode, ...] = Field(default_factory=tuple)
    relations: tuple[Relation, ...] = Field(default_factory=tuple)
    semantic_annotations: tuple[SemanticAnnotation, ...] = Field(default_factory=tuple)
    assets: tuple[Asset, ...] = Field(default_factory=tuple)
    subdocuments: tuple[SubDocument, ...] = Field(default_factory=tuple)
    quality: DocumentQuality = Field(default_factory=DocumentQuality)

    @property
    def source_id(self) -> str:
        return self.document_id

    @model_validator(mode="after")
    def validate_integrity(self) -> CanonicalDocument:
        element_ids = self._unique_ids("elements", self.elements)
        region_ids = self._unique_ids("regions", self.regions)
        logical_ids = self._unique_ids("logical_units", self.logical_units)
        context_ids = self._unique_ids("context_nodes", self.context_nodes)
        asset_ids = self._unique_ids("assets", self.assets)
        subdocument_ids = self._unique_ids("subdocuments", self.subdocuments)
        annotation_ids = self._unique_ids("semantic_annotations", self.semantic_annotations)
        relation_ids = self._unique_ids("relations", self.relations)

        namespaces = {
            "elements": element_ids,
            "regions": region_ids,
            "logical_units": logical_ids,
            "context_nodes": context_ids,
            "assets": asset_ids,
            "subdocuments": subdocument_ids,
            "semantic_annotations": annotation_ids,
            "relations": relation_ids,
        }
        names = list(namespaces)
        for index, left_name in enumerate(names):
            for right_name in names[index + 1 :]:
                collisions = namespaces[left_name] & namespaces[right_name]
                if collisions:
                    raise ValueError(f"id collision between {left_name} and {right_name}: {sorted(collisions)}")

        element_order = self._validate_element_order()

        seen_region_elements: dict[str, str] = {}
        region_by_id = {region.id: region for region in self.regions}
        for region in self.regions:
            self._require_subset(f"content region {region.id} element_ids", region.element_ids, element_ids)
            self._validate_reference_order(f"content region {region.id} element_ids", region.element_ids, element_order)
            for element_id in region.element_ids:
                previous = seen_region_elements.get(element_id)
                if previous is not None:
                    raise ValueError(f"element {element_id!r} belongs to multiple content regions: {previous!r} and {region.id!r}")
                seen_region_elements[element_id] = region.id

        if self.structure.mode == StructureMode.MIXED and not self.regions:
            raise ValueError("MIXED document structure requires content regions")

        for unit in self.logical_units:
            self._require_subset(f"logical unit {unit.id} element_ids", unit.element_ids, element_ids)
            self._validate_reference_order(f"logical unit {unit.id} element_ids", unit.element_ids, element_order)
            self._require_subset(
                f"logical unit {unit.id} context_node_ids",
                unit.context_node_ids,
                context_ids,
            )
            if unit.region_id is not None:
                region = region_by_id.get(unit.region_id)
                if region is None:
                    raise ValueError(f"logical unit {unit.id} references unknown region_id {unit.region_id!r}")
                if not set(unit.element_ids).issubset(region.element_ids):
                    raise ValueError(f"logical unit {unit.id} contains elements outside region {region.id!r}")

        seen_subdocument_elements: dict[str, str] = {}
        for subdoc in self.subdocuments:
            self._require_subset(f"subdocument {subdoc.id} element_ids", subdoc.element_ids, element_ids)
            self._validate_reference_order(f"subdocument {subdoc.id} element_ids", subdoc.element_ids, element_order)
            for element_id in subdoc.element_ids:
                previous = seen_subdocument_elements.get(element_id)
                if previous is not None:
                    raise ValueError(f"element {element_id!r} belongs to multiple subdocuments: {previous!r} and {subdoc.id!r}")
                seen_subdocument_elements[element_id] = subdoc.id

        relation_targets = element_ids | region_ids | logical_ids | context_ids | subdocument_ids
        for relation in self.relations:
            if relation.source_id not in relation_targets:
                raise ValueError(f"relation {relation.id} has unknown source_id {relation.source_id!r}")
            if relation.target_id not in relation_targets:
                raise ValueError(f"relation {relation.id} has unknown target_id {relation.target_id!r}")
            if relation.type == RelationType.PARENT_OF and relation.source_id in context_ids and relation.target_id in context_ids:
                raise ValueError("context hierarchy must use ContextNode.parent_id, not redundant PARENT_OF relations")

        annotation_targets = relation_targets | asset_ids
        elements_by_id = {element.id: element for element in self.elements}
        annotation_element_scopes: dict[str, set[str]] = {element_id: {element_id} for element_id in element_ids}
        annotation_element_scopes.update({unit.id: set(unit.element_ids) for unit in self.logical_units})
        annotation_element_scopes.update({region.id: set(region.element_ids) for region in self.regions})
        annotation_element_scopes.update({subdoc.id: set(subdoc.element_ids) for subdoc in self.subdocuments})
        for annotation in self.semantic_annotations:
            if annotation.target_id not in annotation_targets:
                raise ValueError(f"semantic annotation {annotation.id} targets unknown id {annotation.target_id!r}")
            target_scope = annotation_element_scopes.get(annotation.target_id)
            for evidence in annotation.evidence:
                element = elements_by_id.get(evidence.element_id)
                if element is None:
                    raise ValueError(f"semantic annotation {annotation.id} evidence references unknown element {evidence.element_id!r}")
                if target_scope is not None and evidence.element_id not in target_scope:
                    raise ValueError(f"semantic annotation {annotation.id} evidence element {evidence.element_id!r} is outside target {annotation.target_id!r}")
                text = element.raw_text if evidence.text_view == SemanticTextView.RAW_TEXT else element.normalized_text
                if text is None:
                    raise ValueError(f"semantic annotation {annotation.id} evidence selects missing {evidence.text_view.value} on element {evidence.element_id!r}")
                if evidence.end_char > len(text):
                    raise ValueError(f"semantic annotation {annotation.id} evidence range exceeds element {evidence.element_id!r} {evidence.text_view.value}")
                actual_quote = text[evidence.start_char : evidence.end_char]
                if actual_quote != evidence.quoted_text:
                    raise ValueError(f"semantic annotation {annotation.id} evidence quote does not match element {evidence.element_id!r} {evidence.text_view.value}")

        self._validate_context_tree(context_ids)
        return self

    @staticmethod
    def _unique_ids(name: str, items: tuple[object, ...]) -> set[str]:
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{name} contains duplicate ids")
        return set(ids)

    @staticmethod
    def _require_subset(name: str, refs: tuple[str, ...], valid: set[str]) -> None:
        missing = set(refs) - valid
        if missing:
            raise ValueError(f"{name} contains unknown ids: {sorted(missing)}")

    def _validate_element_order(self) -> dict[str, int]:
        orders = [element.order for element in self.elements]
        if len(orders) != len(set(orders)):
            raise ValueError("elements must have unique order values")
        if orders != sorted(orders):
            raise ValueError("elements must be stored in ascending order")
        return {element.id: element.order for element in self.elements}

    @staticmethod
    def _validate_reference_order(name: str, refs: tuple[str, ...], element_order: dict[str, int]) -> None:
        orders = [element_order[ref] for ref in refs]
        if orders != sorted(orders):
            raise ValueError(f"{name} must follow canonical element order")

    def _validate_context_tree(self, context_ids: set[str]) -> None:
        parents: dict[str, str | None] = {}
        for node in self.context_nodes:
            if node.parent_id is not None and node.parent_id not in context_ids:
                raise ValueError(f"context node {node.id} has unknown parent_id {node.parent_id!r}")
            parents[node.id] = node.parent_id

        for node_id in parents:
            seen: set[str] = set()
            current: str | None = node_id
            while current is not None:
                if current in seen:
                    raise ValueError(f"context hierarchy contains cycle at {current!r}")
                seen.add(current)
                current = parents.get(current)
