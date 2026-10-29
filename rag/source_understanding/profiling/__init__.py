"""Content profiling and region routing for canonical source elements."""

from .content_profiler import (
    CONTENT_PROFILER_VERSION,
    ContentCategory,
    ContentProfile,
    ContentProfiler,
    ContentProfileSignals,
    ContentProfilingError,
    content_category_for_element,
    content_category_for_type,
)
from .lexical_quality import (
    LEXICAL_QUALITY_SCORER_VERSION,
    LexicalAnomalyEvidence,
    LexicalAnomalyReason,
    LexicalLanguage,
    LexicalQualityPolicy,
    LexicalQualityReport,
    LexicalQualityScorer,
)
from .order_quality import (
    ORDER_QUALITY_ESTIMATOR_VERSION,
    OrderQualityError,
    OrderQualityEstimator,
    OrderQualityMetrics,
    OrderQualityPolicy,
    OrderQualityReport,
)
from .regions import (
    CONTENT_REGION_POLICY_VERSION,
    CONTENT_REGION_SEGMENTER_VERSION,
    ContentRegionPolicy,
    ContentRegionSegmentationError,
    ContentRegionSegmentationResult,
    ContentRegionSegmenter,
)
from .text_quality import (
    TEXT_QUALITY_ESTIMATOR_VERSION,
    TextQualityEstimator,
    TextQualityMetrics,
    TextQualityPolicy,
    TextQualityReport,
)

__all__ = [
    "CONTENT_PROFILER_VERSION",
    "CONTENT_REGION_POLICY_VERSION",
    "CONTENT_REGION_SEGMENTER_VERSION",
    "LEXICAL_QUALITY_SCORER_VERSION",
    "ORDER_QUALITY_ESTIMATOR_VERSION",
    "TEXT_QUALITY_ESTIMATOR_VERSION",
    "ContentCategory",
    "ContentProfile",
    "ContentProfileSignals",
    "ContentProfiler",
    "ContentProfilingError",
    "ContentRegionPolicy",
    "ContentRegionSegmentationError",
    "ContentRegionSegmentationResult",
    "ContentRegionSegmenter",
    "LexicalAnomalyEvidence",
    "LexicalAnomalyReason",
    "LexicalLanguage",
    "LexicalQualityPolicy",
    "LexicalQualityReport",
    "LexicalQualityScorer",
    "OrderQualityError",
    "OrderQualityEstimator",
    "OrderQualityMetrics",
    "OrderQualityPolicy",
    "OrderQualityReport",
    "TextQualityEstimator",
    "TextQualityMetrics",
    "TextQualityPolicy",
    "TextQualityReport",
    "content_category_for_element",
    "content_category_for_type",
]
