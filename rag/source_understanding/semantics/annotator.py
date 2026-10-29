from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from pydantic import Field, TypeAdapter, model_validator

from rag.source_understanding.schemas.context import Confidence, Identifier, JsonObject, SchemaModel
from rag.source_understanding.schemas.document import (
    CanonicalDocument,
    SemanticAnnotation,
    SemanticAnnotationType,
    SemanticPayloadMode,
    SemanticTextView,
)
from rag.source_understanding.schemas.logical_unit import LogicalUnit

from .fingerprint import (
    SEMANTIC_CONFIGURATION_FINGERPRINT_VERSION,
    semantic_configuration_hash,
    semantic_provider_capabilities_hash,
)
from .provider import SemanticCandidate, SemanticProvider, SemanticProviderCapabilities, SemanticRequest, SemanticTargetKind
from .quality import SemanticCoverageReport, evaluate_semantic_coverage
from .request import SemanticContextPlanner, SemanticRequestBuilder

SEMANTIC_ANNOTATOR_VERSION = "3"
_RESERVED_METADATA_KEYS = frozenset(
    {
        "semantic_provider",
        "semantic_provider_version",
        "semantic_provider_configuration_hash",
        "semantic_annotator_version",
        "semantic_annotator_policy_hash",
        "request_target_kind",
        "semantic_request_language",
        "semantic_provider_protocol_version",
        "semantic_capability",
        "semantic_payload_mode",
        "semantic_request_truncated",
        "semantic_truncated_element_ids",
        "semantic_omitted_target_element_ids",
        "semantic_omitted_context_element_ids",
        "semantic_ontology_namespace",
        "semantic_ontology_label",
        "semantic_ontology_version",
    }
)
_PROVIDER_CONFIGURATION_ADAPTER = TypeAdapter(JsonObject)


class SemanticAnnotationError(ValueError):
    pass


def _metadata_string_list(metadata: JsonObject, key: str) -> list[str]:
    value = metadata.get(key, [])
    if not isinstance(value, list):
        raise SemanticAnnotationError(f"semantic request metadata {key!r} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SemanticAnnotationError(f"semantic request metadata {key!r} must contain only strings")
        result.append(item)
    return result


_DEFAULT_TYPES = tuple(annotation_type for annotation_type in SemanticAnnotationType if annotation_type != SemanticAnnotationType.CUSTOM)


class SemanticAnnotationPolicy(SchemaModel):
    version: str = Field(default=SEMANTIC_ANNOTATOR_VERSION, min_length=1, max_length=128)
    enabled: bool = True
    annotate_logical_units: bool = True
    annotate_elements: bool = True
    min_confidence: Confidence = 0.65
    max_annotations_per_target: int = Field(default=8, ge=1, le=64)
    max_request_chars: int = Field(default=16000, ge=128, le=32768)
    max_context_elements: int = Field(default=2, ge=0, le=8)
    allowed_types: tuple[SemanticAnnotationType, ...] = _DEFAULT_TYPES
    preserve_existing_annotations: bool = True
    replace_same_provider_annotations: bool = True
    text_separator: str = "\n\n"
    text_view_preference: tuple[SemanticTextView, ...] = (
        SemanticTextView.RAW_TEXT,
        SemanticTextView.NORMALIZED_TEXT,
    )

    @model_validator(mode="after")
    def validate_policy(self) -> SemanticAnnotationPolicy:
        if not self.annotate_logical_units and (not self.annotate_elements):
            raise ValueError("at least one semantic target class must be enabled")
        if len(self.allowed_types) != len(set(self.allowed_types)):
            raise ValueError("allowed_types must be unique")
        if not self.text_separator:
            raise ValueError("text_separator must not be empty")
        if not self.text_view_preference:
            raise ValueError("text_view_preference must not be empty")
        if len(self.text_view_preference) != len(set(self.text_view_preference)):
            raise ValueError("text_view_preference must be unique")
        return self


class SemanticAnnotationResult(SchemaModel):
    version: str = SEMANTIC_ANNOTATOR_VERSION
    document_id: Identifier
    provider_name: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    request_count: int = Field(ge=0)
    provider_candidate_count: int = Field(ge=0)
    accepted_annotation_count: int = Field(ge=0)
    retained_annotation_count: int = Field(ge=0)
    replaced_annotation_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    skipped_low_confidence_count: int = Field(default=0, ge=0)
    skipped_disallowed_type_count: int = Field(default=0, ge=0)
    skipped_duplicate_count: int = Field(default=0, ge=0)
    skipped_target_limit_count: int = Field(default=0, ge=0)
    target_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    coverage: SemanticCoverageReport
    document: CanonicalDocument


class SemanticAnnotator:
    version = SEMANTIC_ANNOTATOR_VERSION

    def __init__(self, provider: SemanticProvider, policy: SemanticAnnotationPolicy | None = None) -> None:
        self._provider = provider
        self._policy = policy if policy is not None else SemanticAnnotationPolicy()
        (
            self._provider_name,
            self._provider_version,
            self._provider_capabilities,
            self._provider_configuration,
        ) = self._validate_provider(provider)
        self._provider_configuration_hash = semantic_configuration_hash(self._provider_configuration)
        self._provider_capabilities_hash = semantic_provider_capabilities_hash(self._provider_capabilities)
        self._policy_configuration = self._policy.model_dump(mode="json")
        self._policy_configuration_hash = semantic_configuration_hash(self._policy_configuration)
        self._request_builder = SemanticRequestBuilder(
            max_request_chars=self._policy.max_request_chars,
            text_separator=self._policy.text_separator,
            text_view_preference=self._policy.text_view_preference,
        )
        self._context_planner = SemanticContextPlanner(max_context_elements=self._policy.max_context_elements)

    def annotate(self, document: CanonicalDocument) -> SemanticAnnotationResult:
        requests = self._build_requests(document)
        target_ids = tuple(request.target_id for request in requests)
        if not self._policy.enabled:
            return SemanticAnnotationResult(
                document_id=document.document_id,
                provider_name=self._provider_name,
                provider_version=self._provider_version,
                request_count=len(requests),
                provider_candidate_count=0,
                accepted_annotation_count=0,
                retained_annotation_count=len(document.semantic_annotations),
                target_ids=target_ids,
                coverage=evaluate_semantic_coverage(document, target_ids),
                document=document,
            )
        try:
            raw_candidates = tuple(self._provider.annotate(requests))
        except Exception as exc:
            raise SemanticAnnotationError(f"semantic provider {self._provider_name!r} failed: {exc}") from exc
        candidates = tuple(self._coerce_candidate(candidate) for candidate in raw_candidates)
        accepted, diagnostics = self._select_candidates(requests, candidates)
        retained, replaced_ids = self._retained_annotations(document.semantic_annotations)
        generated = tuple((self._to_annotation(document, request, capability_name, candidate) for request, capability_name, candidate in accepted))
        annotations = retained + generated
        enriched_document = self._rebuild_document(document, annotations)
        return SemanticAnnotationResult(
            document_id=document.document_id,
            provider_name=self._provider_name,
            provider_version=self._provider_version,
            request_count=len(requests),
            provider_candidate_count=len(candidates),
            accepted_annotation_count=len(generated),
            retained_annotation_count=len(retained),
            replaced_annotation_ids=replaced_ids,
            skipped_low_confidence_count=diagnostics["low_confidence"],
            skipped_disallowed_type_count=diagnostics["disallowed_type"],
            skipped_duplicate_count=diagnostics["duplicate"],
            skipped_target_limit_count=diagnostics["target_limit"],
            target_ids=target_ids,
            coverage=evaluate_semantic_coverage(enriched_document, target_ids),
            document=enriched_document,
        )

    def _build_requests(self, document: CanonicalDocument) -> tuple[SemanticRequest, ...]:
        elements = {element.id: element for element in document.elements}
        source_elements = tuple(document.elements)
        contexts = {node.id: node for node in document.context_nodes}
        units_by_element: dict[str, list[LogicalUnit]] = defaultdict(list)
        requests: list[SemanticRequest] = []
        for logical_unit in document.logical_units:
            for element_id in logical_unit.element_ids:
                units_by_element[element_id].append(logical_unit)
        if self._policy.annotate_logical_units:
            for logical_unit in document.logical_units:
                members = tuple(elements[element_id] for element_id in logical_unit.element_ids)
                unit_context_labels = tuple(contexts[context_id].label for context_id in logical_unit.context_node_ids)
                request = self._request_builder.build(
                    target_id=logical_unit.id,
                    target_kind=SemanticTargetKind.LOGICAL_UNIT,
                    target_elements=members,
                    language=document.metadata.language,
                    logical_unit_type=logical_unit.type.value,
                    unit_label=logical_unit.label,
                    context_labels=unit_context_labels,
                    metadata={"structure_source": logical_unit.source.value, "structure_confidence": logical_unit.confidence},
                    context_elements=(),
                )
                if request is not None:
                    requests.append(request)
        if self._policy.annotate_elements:
            for element in document.elements:
                owners = tuple(units_by_element.get(element.id, ()))
                element_context_labels: list[str] = []
                seen_contexts: set[str] = set()
                for owner in owners:
                    for context_id in owner.context_node_ids:
                        if context_id in seen_contexts:
                            continue
                        seen_contexts.add(context_id)
                        element_context_labels.append(contexts[context_id].label)
                logical_unit_type = owners[0].type.value if len(owners) == 1 else None
                unit_label = owners[0].label if len(owners) == 1 else None
                owner_element_ids = frozenset(element_id for owner in owners for element_id in owner.element_ids) or None
                context_elements = self._context_planner.plan(
                    target_elements=(element,),
                    source_elements=source_elements,
                    scope_element_ids=owner_element_ids,
                )
                request = self._request_builder.build(
                    target_id=element.id,
                    target_kind=SemanticTargetKind.ELEMENT,
                    target_elements=(element,),
                    language=document.metadata.language,
                    logical_unit_type=logical_unit_type,
                    unit_label=unit_label,
                    context_labels=tuple(element_context_labels),
                    metadata={"element_type": element.type.value, "logical_unit_ids": [owner.id for owner in owners]},
                    context_elements=context_elements,
                )
                if request is not None:
                    requests.append(request)
        return tuple(request for request in requests if self._provider_capabilities.supports_target_kind(request.target_kind))

    def _select_candidates(self, requests: tuple[SemanticRequest, ...], candidates: tuple[SemanticCandidate, ...]) -> tuple[tuple[tuple[SemanticRequest, str, SemanticCandidate], ...], dict[str, int]]:
        request_by_target = {request.target_id: request for request in requests}
        allowed_types = set(self._policy.allowed_types)
        diagnostics = {"low_confidence": 0, "disallowed_type": 0, "duplicate": 0, "target_limit": 0}
        by_target: dict[str, list[tuple[int, str, SemanticCandidate]]] = defaultdict(list)
        for index, candidate in enumerate(candidates):
            request = request_by_target.get(candidate.target_id)
            if request is None:
                raise SemanticAnnotationError(f"provider {self._provider_name!r} returned annotation for target {candidate.target_id!r} that was not requested")
            try:
                capability = self._provider_capabilities.resolve_candidate_capability(
                    request.target_kind,
                    candidate.type,
                    candidate.ontology,
                    candidate.capability_name,
                )
            except ValueError as exc:
                ontology_label = candidate.ontology.key if candidate.ontology is not None else None
                raise SemanticAnnotationError(
                    f"provider {self._provider_name!r} returned undeclared capability or ambiguous capability output: target_kind={request.target_kind.value}, type={candidate.type.value}, ontology={ontology_label!r}: {exc}"
                ) from exc
            self._validate_candidate_evidence(request, candidate)
            if candidate.confidence < self._policy.min_confidence:
                diagnostics["low_confidence"] += 1
                continue
            if candidate.type not in allowed_types:
                diagnostics["disallowed_type"] += 1
                continue
            by_target[candidate.target_id].append((index, capability.name, candidate))
        selected: list[tuple[SemanticRequest, str, SemanticCandidate]] = []
        for request in requests:
            target_candidates = by_target.get(request.target_id, [])
            deduplicated: dict[tuple[str, str, str], tuple[int, str, SemanticCandidate]] = {}
            for provider_index, capability_name, candidate in target_candidates:
                key = (candidate.type.value, candidate.ontology.key if candidate.ontology is not None else "", candidate.value.strip().casefold())
                previous = deduplicated.get(key)
                if previous is None:
                    deduplicated[key] = (provider_index, capability_name, candidate)
                    continue
                diagnostics["duplicate"] += 1
                previous_index, _, previous_candidate = previous
                if candidate.confidence > previous_candidate.confidence or candidate.confidence == previous_candidate.confidence and provider_index < previous_index:
                    deduplicated[key] = (provider_index, capability_name, candidate)
            ordered = sorted(deduplicated.values(), key=lambda item: (-item[2].confidence, item[0]))
            if len(ordered) > self._policy.max_annotations_per_target:
                diagnostics["target_limit"] += len(ordered) - self._policy.max_annotations_per_target
                ordered = ordered[: self._policy.max_annotations_per_target]
            selected.extend(((request, capability_name, candidate) for _, capability_name, candidate in ordered))
        return (tuple(selected), diagnostics)

    def _retained_annotations(self, existing: tuple[SemanticAnnotation, ...]) -> tuple[tuple[SemanticAnnotation, ...], tuple[str, ...]]:
        if not self._policy.preserve_existing_annotations:
            return ((), tuple(annotation.id for annotation in existing))
        retained: list[SemanticAnnotation] = []
        replaced: list[str] = []
        for annotation in existing:
            same_provider = annotation.metadata.get("semantic_provider") == self._provider_name
            if same_provider and self._policy.replace_same_provider_annotations:
                replaced.append(annotation.id)
                continue
            retained.append(annotation)
        return (tuple(retained), tuple(replaced))

    def _to_annotation(self, document: CanonicalDocument, request: SemanticRequest, capability_name: str, candidate: SemanticCandidate) -> SemanticAnnotation:
        candidate_metadata = dict(candidate.metadata)
        collision = _RESERVED_METADATA_KEYS & candidate_metadata.keys()
        if collision:
            raise SemanticAnnotationError(f"provider candidate metadata uses reserved semantic keys: {sorted(collision)}")
        payload_mode = candidate.payload_mode or SemanticPayloadMode.LABEL_ONLY
        metadata = {
            **candidate_metadata,
            "semantic_provider": self._provider_name,
            "semantic_provider_version": self._provider_version,
            "semantic_provider_configuration_hash": self._provider_configuration_hash,
            "semantic_annotator_version": self.version,
            "semantic_annotator_policy_hash": self._policy_configuration_hash,
            "semantic_provider_protocol_version": self._provider_capabilities.protocol_version,
            "request_target_kind": request.target_kind.value,
            "semantic_request_language": request.language or "und",
            "semantic_capability": capability_name,
            "semantic_payload_mode": payload_mode.value,
            "semantic_request_truncated": bool(request.metadata.get("request_truncated", False)),
            "semantic_truncated_element_ids": _metadata_string_list(request.metadata, "truncated_element_ids"),
            "semantic_omitted_target_element_ids": _metadata_string_list(request.metadata, "omitted_target_element_ids"),
            "semantic_omitted_context_element_ids": _metadata_string_list(request.metadata, "omitted_context_element_ids"),
        }
        if candidate.ontology is not None:
            metadata["semantic_ontology_namespace"] = candidate.ontology.namespace
            metadata["semantic_ontology_label"] = candidate.ontology.label
            if candidate.ontology.version is not None:
                metadata["semantic_ontology_version"] = candidate.ontology.version
        value = candidate.value.strip()
        return SemanticAnnotation(
            id=self._annotation_id(document, candidate, metadata),
            target_id=candidate.target_id,
            type=candidate.type,
            value=value,
            payload_mode=payload_mode,
            source=candidate.source,
            confidence=candidate.confidence,
            confidence_method=candidate.confidence_method,
            calibration_version=candidate.calibration_version,
            evidence=candidate.evidence,
            model_version=self._model_version(),
            metadata=metadata,
        )

    def _annotation_id(self, document: CanonicalDocument, candidate: SemanticCandidate, metadata: dict[str, object]) -> str:
        payload = {
            "annotator_version": self.version,
            "document_id": document.document_id,
            "content_hash": document.content_hash,
            "source_revision": document.source_revision,
            "provider": self._provider_name,
            "provider_version": self._provider_version,
            "target_id": candidate.target_id,
            "type": candidate.type.value,
            "value": candidate.value.strip(),
            "payload_mode": (candidate.payload_mode or SemanticPayloadMode.LABEL_ONLY).value,
            "source": candidate.source.value,
            "confidence": candidate.confidence,
            "confidence_method": candidate.confidence_method.value,
            "calibration_version": candidate.calibration_version,
            "evidence": [item.model_dump(mode="json") for item in candidate.evidence],
            "metadata": metadata,
        }
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()[:24]
        return f"sem_{digest}"

    def _rebuild_document(self, document: CanonicalDocument, annotations: tuple[SemanticAnnotation, ...]) -> CanonicalDocument:
        processing_configuration = dict(document.processing.configuration)
        previous_semantic = processing_configuration.get("semantic_understanding", {})
        previous_semantic = dict(previous_semantic) if isinstance(previous_semantic, dict) else {}
        providers = previous_semantic.get("providers", {})
        providers = dict(providers) if isinstance(providers, dict) else {}
        for annotation in annotations:
            provider_name = annotation.metadata.get("semantic_provider")
            provider_version = annotation.metadata.get("semantic_provider_version")
            if isinstance(provider_name, str) and isinstance(provider_version, str):
                providers[provider_name] = provider_version
        providers[self._provider_name] = self._provider_version
        provider_capabilities = previous_semantic.get("provider_capabilities", {})
        provider_capabilities = dict(provider_capabilities) if isinstance(provider_capabilities, dict) else {}
        provider_capabilities[self._provider_name] = self._provider_capabilities.model_dump(mode="json")
        provider_configurations = previous_semantic.get("provider_configurations", {})
        provider_configurations = dict(provider_configurations) if isinstance(provider_configurations, dict) else {}
        provider_configurations[self._provider_name] = dict(self._provider_configuration)
        provider_configuration_hashes = previous_semantic.get("provider_configuration_hashes", {})
        provider_configuration_hashes = dict(provider_configuration_hashes) if isinstance(provider_configuration_hashes, dict) else {}
        provider_configuration_hashes[self._provider_name] = self._provider_configuration_hash
        provider_annotator_policies = previous_semantic.get("provider_annotator_policies", {})
        provider_annotator_policies = dict(provider_annotator_policies) if isinstance(provider_annotator_policies, dict) else {}
        provider_annotator_policies[self._provider_name] = dict(self._policy_configuration)
        provider_annotator_policy_hashes = previous_semantic.get("provider_annotator_policy_hashes", {})
        provider_annotator_policy_hashes = dict(provider_annotator_policy_hashes) if isinstance(provider_annotator_policy_hashes, dict) else {}
        provider_annotator_policy_hashes[self._provider_name] = self._policy_configuration_hash
        provider_capability_hashes = previous_semantic.get("provider_capability_hashes", {})
        provider_capability_hashes = dict(provider_capability_hashes) if isinstance(provider_capability_hashes, dict) else {}
        provider_capability_hashes[self._provider_name] = self._provider_capabilities_hash
        processing_configuration["semantic_understanding"] = {
            "annotator_version": self.version,
            "policy_version": self._policy.version,
            "configuration_fingerprint_version": SEMANTIC_CONFIGURATION_FINGERPRINT_VERSION,
            "providers": dict(sorted(providers.items())),
            "provider_capabilities": dict(sorted(provider_capabilities.items())),
            "provider_capability_hashes": dict(sorted(provider_capability_hashes.items())),
            "provider_configurations": dict(sorted(provider_configurations.items())),
            "provider_configuration_hashes": dict(sorted(provider_configuration_hashes.items())),
            "provider_annotator_policies": dict(sorted(provider_annotator_policies.items())),
            "provider_annotator_policy_hashes": dict(sorted(provider_annotator_policy_hashes.items())),
        }
        processing = document.processing.model_copy(update={"semantic_version": self._semantic_version(), "configuration": processing_configuration})
        payload = document.model_dump(mode="python")
        payload["processing"] = processing
        payload["semantic_annotations"] = annotations
        try:
            return CanonicalDocument.model_validate(payload)
        except ValueError as exc:
            raise SemanticAnnotationError(f"semantic annotations failed CanonicalDocument validation: {exc}") from exc

    def _validate_candidate_evidence(
        self,
        request: SemanticRequest,
        candidate: SemanticCandidate,
    ) -> None:
        # Context is inference-only.  Supporting context is deliberately not
        # accepted as annotation evidence because canonical evidence must remain
        # inside the semantic target scope.
        segments = request.target_segments
        for evidence in candidate.evidence:
            matched = False
            for segment in segments:
                if segment.element_id != evidence.element_id or segment.text_view != evidence.text_view or evidence.start_char < segment.element_start or evidence.end_char > segment.element_end:
                    continue
                local_start = evidence.start_char - segment.element_start
                local_end = evidence.end_char - segment.element_start
                if segment.text[local_start:local_end] != evidence.quoted_text:
                    raise SemanticAnnotationError(f"provider {self._provider_name!r} returned evidence whose quote does not match request segment for target {candidate.target_id!r}")
                matched = True
                break
            if not matched:
                raise SemanticAnnotationError(
                    f"provider {self._provider_name!r} returned evidence outside the "
                    f"segmented request for target {candidate.target_id!r}: "
                    f"{evidence.element_id!r}[{evidence.start_char}:{evidence.end_char}]"
                )

    def _model_version(self) -> str:
        value = f"{self._provider_name}:{self._provider_version}"
        if len(value) > 128:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            value = f"provider:{digest}"
        return value

    def _semantic_version(self) -> str:
        return f"semantic-annotations:{self.version}"

    @staticmethod
    def _validate_provider(provider: SemanticProvider) -> tuple[str, str, SemanticProviderCapabilities, JsonObject]:
        annotate = getattr(provider, "annotate", None)
        name = getattr(provider, "name", None)
        version = getattr(provider, "version", None)
        capabilities = getattr(provider, "capabilities", None)
        if not callable(annotate):
            raise TypeError("semantic provider must define callable annotate(requests)")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
            raise TypeError("semantic provider name must be a non-blank string <= 128 chars")
        if not isinstance(version, str) or not version.strip() or len(version.strip()) > 128:
            raise TypeError("semantic provider version must be a non-blank string <= 128 chars")
        try:
            capabilities = SemanticProviderCapabilities.model_validate(capabilities)
        except Exception as exc:
            raise TypeError("semantic provider must expose valid capability declaration") from exc
        raw_configuration = getattr(provider, "configuration", {})
        try:
            configuration = _PROVIDER_CONFIGURATION_ADAPTER.validate_python(raw_configuration)
        except Exception as exc:
            raise TypeError("semantic provider configuration must be JSON-safe and finite") from exc
        return (name.strip(), version.strip(), capabilities, configuration)

    @staticmethod
    def _coerce_candidate(candidate: object) -> SemanticCandidate:
        if isinstance(candidate, SemanticCandidate):
            return candidate
        try:
            return SemanticCandidate.model_validate(candidate)
        except Exception as exc:
            raise SemanticAnnotationError(f"semantic provider returned invalid candidate {candidate!r}: {exc}") from exc
