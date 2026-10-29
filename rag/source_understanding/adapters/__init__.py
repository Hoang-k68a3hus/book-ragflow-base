"""DeepDOC adapter boundary used by RAGFlow book ingestion."""

from .base import (
    SOURCE_ADAPTER_PROTOCOL_VERSION,
    AdapterDiagnostic,
    AdapterDiagnosticLevel,
    AdapterError,
    SourceAdapter,
    SourceAdapterResult,
    validate_adapter,
)
from .deepdoc import (
    DEEPDOC_PDF_MEDIA_TYPE,
    DEEPDOC_PDF_OBSERVATION_BUILDER_VERSION,
    DEEPDOC_SOURCE_ADAPTER_VERSION,
    DeepDocPdfObservationBuilder,
    DeepDocSourceAdapter,
)
from .runner import (
    SOURCE_ADAPTER_RUNNER_VERSION,
    AdaptedSourceUnderstandingResult,
    SourceAdapterRunner,
)

__all__ = [
    "DEEPDOC_PDF_MEDIA_TYPE",
    "DEEPDOC_PDF_OBSERVATION_BUILDER_VERSION",
    "DEEPDOC_SOURCE_ADAPTER_VERSION",
    "SOURCE_ADAPTER_PROTOCOL_VERSION",
    "SOURCE_ADAPTER_RUNNER_VERSION",
    "AdaptedSourceUnderstandingResult",
    "AdapterDiagnostic",
    "AdapterDiagnosticLevel",
    "AdapterError",
    "DeepDocPdfObservationBuilder",
    "DeepDocSourceAdapter",
    "SourceAdapter",
    "SourceAdapterResult",
    "SourceAdapterRunner",
    "validate_adapter",
]
