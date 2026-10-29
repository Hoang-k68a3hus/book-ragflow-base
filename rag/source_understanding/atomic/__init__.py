"""Source-near normalization into canonical Elements."""

from .normalizer import (
    ELEMENT_NORMALIZER_POLICY_VERSION,
    ELEMENT_NORMALIZER_VERSION,
    ElementNormalizationError,
    ElementNormalizationPolicy,
    ElementNormalizationResult,
    ElementNormalizer,
    UnicodeNormalizationForm,
)
from .preservation import (
    PARSER_PRESERVATION_EVALUATOR_VERSION,
    ParserPreservationReport,
    evaluate_parser_preservation,
)

__all__ = [
    "ELEMENT_NORMALIZER_POLICY_VERSION",
    "ELEMENT_NORMALIZER_VERSION",
    "PARSER_PRESERVATION_EVALUATOR_VERSION",
    "ElementNormalizationError",
    "ElementNormalizationPolicy",
    "ElementNormalizationResult",
    "ElementNormalizer",
    "ParserPreservationReport",
    "UnicodeNormalizationForm",
    "evaluate_parser_preservation",
]
