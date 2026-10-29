"""Optional semantic understanding after structural CanonicalDocument assembly."""

from .annotator import (
    SEMANTIC_ANNOTATOR_VERSION,
    SemanticAnnotationError,
    SemanticAnnotationPolicy,
    SemanticAnnotationResult,
    SemanticAnnotator,
)
from .fingerprint import (
    SEMANTIC_CONFIGURATION_FINGERPRINT_VERSION,
    semantic_configuration_hash,
    semantic_provider_capabilities_hash,
)
from .provider import (
    EXTRACTIVE_SEMANTIC_ANNOTATION_TYPES,
    SEMANTIC_PROVIDER_PROTOCOL_VERSION,
    SemanticCandidate,
    SemanticCapability,
    SemanticOntologyLabel,
    SemanticProvider,
    SemanticProviderCapabilities,
    SemanticRequest,
    SemanticRequestSegment,
    SemanticTargetKind,
)
from .quality import SemanticCoverageReport, evaluate_semantic_coverage
from .request import (
    SEMANTIC_REQUEST_BUILDER_VERSION,
    SemanticContextPlanner,
    SemanticRequestBuilder,
)

__all__ = [
    "EXTRACTIVE_SEMANTIC_ANNOTATION_TYPES",
    "SEMANTIC_ANNOTATOR_VERSION",
    "SEMANTIC_CONFIGURATION_FINGERPRINT_VERSION",
    "SEMANTIC_PROVIDER_PROTOCOL_VERSION",
    "SEMANTIC_REQUEST_BUILDER_VERSION",
    "SemanticAnnotationError",
    "SemanticAnnotationPolicy",
    "SemanticAnnotationResult",
    "SemanticAnnotator",
    "SemanticCandidate",
    "SemanticCapability",
    "SemanticContextPlanner",
    "SemanticCoverageReport",
    "SemanticOntologyLabel",
    "SemanticProvider",
    "SemanticProviderCapabilities",
    "SemanticRequest",
    "SemanticRequestBuilder",
    "SemanticRequestSegment",
    "SemanticTargetKind",
    "evaluate_semantic_coverage",
    "semantic_configuration_hash",
    "semantic_provider_capabilities_hash",
]
