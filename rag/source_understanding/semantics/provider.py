from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import (
    Confidence,
    Identifier,
    JsonObject,
    SchemaModel,
    StructureSource,
)
from rag.source_understanding.schemas.document import (
    SemanticAnnotationType,
    SemanticConfidenceMethod,
    SemanticEvidenceSpan,
    SemanticPayloadMode,
    SemanticTextView,
    semantic_extractive_value_key,
    semantic_payload_mode_for_type,
)

SEMANTIC_PROVIDER_PROTOCOL_VERSION = "3"


EXTRACTIVE_SEMANTIC_ANNOTATION_TYPES = frozenset(
    {
        SemanticAnnotationType.CONCEPT,
        SemanticAnnotationType.ENTITY,
        SemanticAnnotationType.KEYWORD,
    }
)


class SemanticTargetKind(StrEnum):
    LOGICAL_UNIT = "LOGICAL_UNIT"
    ELEMENT = "ELEMENT"


class SemanticOntologyLabel(SchemaModel):
    """Provider-owned fine-grained label under a stable namespace.

    Core schemas keep coarse annotation types stable. Specialized providers can
    refine them with namespaced labels such as ``ner:PERSON`` or
    ``temporal:EVENT`` without expanding the global enum.
    """

    namespace: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")
    version: str | None = Field(default=None, min_length=1, max_length=128)

    @property
    def key(self) -> str:
        return f"{self.namespace}:{self.label}"


class SemanticCapability(SchemaModel):
    """One independently routable semantic capability advertised by a provider."""

    name: str = Field(min_length=1, max_length=128)
    target_kinds: tuple[SemanticTargetKind, ...] = Field(min_length=1)
    annotation_types: tuple[SemanticAnnotationType, ...] = Field(min_length=1)
    ontology_namespaces: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_capability(self) -> SemanticCapability:
        if len(self.target_kinds) != len(set(self.target_kinds)):
            raise ValueError("semantic capability target_kinds must be unique")
        if len(self.annotation_types) != len(set(self.annotation_types)):
            raise ValueError("semantic capability annotation_types must be unique")
        if len(self.ontology_namespaces) != len(set(self.ontology_namespaces)):
            raise ValueError("semantic capability ontology_namespaces must be unique")
        for namespace in self.ontology_namespaces:
            if not namespace or namespace.strip() != namespace:
                raise ValueError("ontology namespaces must be non-blank and trimmed")
            if len(namespace) > 128:
                raise ValueError("ontology namespace must be <= 128 chars")
            if re.fullmatch(r"[A-Za-z0-9_.-]+", namespace) is None:
                raise ValueError("ontology namespace must match [A-Za-z0-9_.-]+")
        return self


class SemanticProviderCapabilities(SchemaModel):
    """Capability declaration used for routing and provider-output validation."""

    protocol_version: str = SEMANTIC_PROVIDER_PROTOCOL_VERSION
    capabilities: tuple[SemanticCapability, ...] = Field(min_length=1)
    deterministic: bool | None = None

    @model_validator(mode="after")
    def validate_capabilities(self) -> SemanticProviderCapabilities:
        if self.protocol_version != SEMANTIC_PROVIDER_PROTOCOL_VERSION:
            raise ValueError(f"unsupported semantic provider protocol_version: {self.protocol_version!r} != {SEMANTIC_PROVIDER_PROTOCOL_VERSION!r}")
        names = [capability.name for capability in self.capabilities]
        if len(names) != len(set(names)):
            raise ValueError("semantic capability names must be unique")
        return self

    def supports_target_kind(self, target_kind: SemanticTargetKind) -> bool:
        return any(target_kind in capability.target_kinds for capability in self.capabilities)

    def supports_candidate(
        self,
        target_kind: SemanticTargetKind,
        annotation_type: SemanticAnnotationType,
        ontology: SemanticOntologyLabel | None,
    ) -> bool:
        for capability in self.capabilities:
            if target_kind not in capability.target_kinds:
                continue
            if annotation_type not in capability.annotation_types:
                continue
            if ontology is None:
                return True
            if ontology.namespace in capability.ontology_namespaces:
                return True
        return False

    def resolve_candidate_capability(
        self,
        target_kind: SemanticTargetKind,
        annotation_type: SemanticAnnotationType,
        ontology: SemanticOntologyLabel | None,
        requested_name: str | None = None,
    ) -> SemanticCapability:
        matches = tuple(
            capability
            for capability in self.capabilities
            if target_kind in capability.target_kinds
            and annotation_type in capability.annotation_types
            and (ontology is None or ontology.namespace in capability.ontology_namespaces)
            and (requested_name is None or capability.name == requested_name)
        )
        if not matches:
            ontology_label = ontology.key if ontology is not None else None
            raise ValueError(f"no declared semantic capability matches target_kind={target_kind.value}, type={annotation_type.value}, ontology={ontology_label!r}, requested_name={requested_name!r}")
        if len(matches) > 1:
            raise ValueError(f"semantic candidate matches multiple capabilities; provider must set capability_name explicitly: {[item.name for item in matches]}")
        return next(iter(matches))


class SemanticRequestSegment(SchemaModel):
    """A reversible slice from provider request text to one Element text view."""

    element_id: Identifier
    text: str = Field(min_length=1, max_length=32768)
    text_view: SemanticTextView
    element_start: int = Field(ge=0)
    element_end: int = Field(gt=0)
    request_start: int = Field(ge=0)
    request_end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> SemanticRequestSegment:
        if self.element_end <= self.element_start:
            raise ValueError("semantic request segment element range must be non-empty")
        if self.request_end <= self.request_start:
            raise ValueError("semantic request segment request range must be non-empty")
        if self.element_end - self.element_start != len(self.text):
            raise ValueError("semantic request segment element range length must equal text length")
        if self.request_end - self.request_start != len(self.text):
            raise ValueError("semantic request segment request range length must equal text length")
        return self


class SemanticRequest(SchemaModel):
    """Source-grounded text view passed to a semantic provider.

    A request never changes canonical source state. Providers may infer semantic
    labels from this view, but those labels remain semantic enrichment.
    """

    target_id: Identifier
    target_kind: SemanticTargetKind
    text: str = Field(min_length=1, max_length=32768)
    language: str | None = Field(default=None, min_length=2, max_length=64)
    element_ids: tuple[Identifier, ...] = Field(min_length=1)
    target_segments: tuple[SemanticRequestSegment, ...] = Field(min_length=1)
    context_segments: tuple[SemanticRequestSegment, ...] = Field(default_factory=tuple)
    logical_unit_type: str | None = Field(default=None, max_length=128)
    unit_label: str | None = Field(default=None, max_length=2048)
    context_labels: tuple[str, ...] = Field(default_factory=tuple)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_request(self) -> SemanticRequest:
        if not self.text.strip():
            raise ValueError("semantic request text must not be blank")
        if len(self.element_ids) != len(set(self.element_ids)):
            raise ValueError("semantic request element_ids must be unique")
        target_segment_ids = [segment.element_id for segment in self.target_segments]
        if len(target_segment_ids) != len(set(target_segment_ids)):
            raise ValueError("semantic request target segment element_ids must be unique")
        if not set(target_segment_ids).issubset(self.element_ids):
            raise ValueError("semantic request target segments must reference target element_ids")
        if self.target_kind == SemanticTargetKind.ELEMENT and (len(self.element_ids) != 1 or set(target_segment_ids) != set(self.element_ids)):
            raise ValueError("ELEMENT semantic requests require one matching target segment")
        segments = sorted(
            (*self.target_segments, *self.context_segments),
            key=lambda segment: segment.request_start,
        )
        previous_end = 0
        for segment in segments:
            if segment.request_start < previous_end:
                raise ValueError("semantic request segments must not overlap")
            if segment.request_end > len(self.text):
                raise ValueError("semantic request segment exceeds request text")
            if self.text[segment.request_start : segment.request_end] != segment.text:
                raise ValueError("semantic request segment text does not match request text range")
            previous_end = segment.request_end
        return self


class SemanticCandidate(SchemaModel):
    """Provider output before canonical SemanticAnnotation construction."""

    target_id: Identifier
    type: SemanticAnnotationType
    value: str = Field(min_length=1, max_length=8192)
    payload_mode: SemanticPayloadMode | None = None
    confidence: Confidence
    confidence_method: SemanticConfidenceMethod = SemanticConfidenceMethod.UNCALIBRATED
    calibration_version: str | None = Field(default=None, min_length=1, max_length=128)
    source: StructureSource = StructureSource.INFERRED
    capability_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    ontology: SemanticOntologyLabel | None = None
    evidence: tuple[SemanticEvidenceSpan, ...] = Field(default_factory=tuple)
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
    def validate_candidate(self) -> SemanticCandidate:
        if self.source == StructureSource.EXPLICIT:
            raise ValueError("semantic providers cannot promote inferred output to EXPLICIT source fact")
        if not self.value.strip():
            raise ValueError("semantic candidate value must not be blank")
        if self.type == SemanticAnnotationType.CUSTOM and self.ontology is None:
            raise ValueError("CUSTOM semantic candidates require a namespaced ontology label")
        if self.confidence_method == SemanticConfidenceMethod.CALIBRATED_PROBABILITY and self.calibration_version is None:
            raise ValueError("CALIBRATED_PROBABILITY semantic confidence requires calibration_version")
        if self.type in EXTRACTIVE_SEMANTIC_ANNOTATION_TYPES and not self.evidence:
            raise ValueError(f"{self.type.value} semantic candidates require source evidence")
        if self.type in EXTRACTIVE_SEMANTIC_ANNOTATION_TYPES and semantic_extractive_value_key(self.value) not in {semantic_extractive_value_key(item.quoted_text) for item in self.evidence}:
            raise ValueError(f"{self.type.value} semantic candidate value must match an evidence quote")
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
            raise ValueError("semantic candidate evidence spans must be unique")
        return self


@runtime_checkable
class SemanticProvider(Protocol):
    """Synchronous capability-declared provider for optional semantic understanding."""

    name: str
    version: str
    capabilities: SemanticProviderCapabilities

    def annotate(
        self,
        requests: tuple[SemanticRequest, ...],
    ) -> Iterable[SemanticCandidate]: ...
