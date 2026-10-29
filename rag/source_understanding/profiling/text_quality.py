from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence

from pydantic import Field

from rag.source_understanding.schemas.context import Confidence, SchemaModel
from rag.source_understanding.schemas.document import DocumentQuality, LexicalQuality
from rag.source_understanding.schemas.element import Element

from .lexical_quality import LexicalQualityReport, LexicalQualityScorer

TEXT_QUALITY_ESTIMATOR_VERSION = "2"

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_SUSPICIOUS_SPACING_RE = re.compile(
    r"\b[bcdfghjklmnpqrstvwxyz\u0111]\s+(?=[a-z\u00e0-\u1ef9]{2,}\b)",
    re.IGNORECASE,
)
_MOJIBAKE_MARKERS = (
    "\u00c3\u0192",
    "\u00c3\u201a",
    "\u00e2\u20ac",
    "\u00ef\u00bf\u00bd",
    "\ufffd",
)


class TextQualityPolicy(SchemaModel):
    version: str = TEXT_QUALITY_ESTIMATOR_VERSION
    character_error_weight: float = Field(default=4.0, ge=0.0, le=20.0)
    suspicious_spacing_weight: float = Field(default=3.0, ge=0.0, le=20.0)
    duplicate_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    lexical_anomaly_weight: float = Field(default=1.5, ge=0.0, le=20.0)
    low_quality_warning_threshold: Confidence = 0.90
    suspicious_spacing_warning_threshold: Confidence = 0.005
    duplicate_warning_threshold: Confidence = 0.10
    minimum_duplicate_text_length: int = Field(default=20, ge=1, le=4096)


class TextQualityMetrics(SchemaModel):
    element_count: int = Field(ge=1)
    text_element_count: int = Field(ge=0)
    character_count: int = Field(ge=0)
    non_whitespace_character_count: int = Field(ge=0)
    word_count: int = Field(ge=0)
    replacement_character_count: int = Field(ge=0)
    control_character_count: int = Field(ge=0)
    private_use_character_count: int = Field(ge=0)
    mojibake_marker_count: int = Field(ge=0)
    suspicious_spacing_count: int = Field(ge=0)
    duplicate_text_element_count: int = Field(ge=0)
    character_error_ratio: Confidence
    suspicious_spacing_ratio: Confidence
    duplicate_ratio: Confidence
    lexical_anomaly_count: int = Field(ge=0)
    lexical_anomaly_ratio: Confidence


class TextQualityReport(SchemaModel):
    version: str = TEXT_QUALITY_ESTIMATOR_VERSION
    policy: TextQualityPolicy
    metrics: TextQualityMetrics
    quality: DocumentQuality
    lexical_quality: LexicalQualityReport


class TextQualityEstimator:
    """Measure extraction symptoms without rewriting or guessing source text."""

    version = TEXT_QUALITY_ESTIMATOR_VERSION

    def __init__(
        self,
        policy: TextQualityPolicy | None = None,
        lexical_scorer: LexicalQualityScorer | None = None,
    ) -> None:
        self._policy = policy if policy is not None else TextQualityPolicy()
        self._lexical_scorer = lexical_scorer if lexical_scorer is not None else LexicalQualityScorer()

    def estimate(
        self,
        elements: Sequence[Element],
        *,
        language_hint: str | None = None,
    ) -> TextQualityReport:
        snapshot = tuple(elements)
        if not snapshot:
            raise ValueError("cannot estimate text quality for an empty source")

        texts = tuple(element.text for element in snapshot if element.text is not None and element.text.strip())
        joined = "\n".join(texts)
        non_whitespace = sum(not char.isspace() for char in joined)
        replacement_count = joined.count("\ufffd")
        control_count = sum(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in joined)
        private_use_count = sum(unicodedata.category(char) == "Co" for char in joined)
        mojibake_count = sum(joined.count(marker) for marker in _MOJIBAKE_MARKERS)
        words = _WORD_RE.findall(joined)
        suspicious_spacing_count = len(_SUSPICIOUS_SPACING_RE.findall(joined))

        normalized = [" ".join(text.split()).casefold() for text in texts]
        duplicate_candidates = [text for text in normalized if len(text) >= self._policy.minimum_duplicate_text_length]
        counts = Counter(duplicate_candidates)
        duplicate_count = sum(count - 1 for count in counts.values() if count > 1)

        character_error_count = replacement_count + control_count + private_use_count + mojibake_count
        character_error_ratio = character_error_count / max(1, non_whitespace)
        suspicious_spacing_ratio = suspicious_spacing_count / max(1, len(words))
        duplicate_ratio = duplicate_count / max(1, len(texts))
        lexical_quality = self._lexical_scorer.score(snapshot, language_hint=language_hint)
        garbage_ratio = min(1.0, character_error_ratio + suspicious_spacing_ratio)
        penalty = min(
            1.0,
            self._policy.character_error_weight * character_error_ratio
            + self._policy.suspicious_spacing_weight * suspicious_spacing_ratio
            + self._policy.duplicate_weight * duplicate_ratio
            + self._policy.lexical_anomaly_weight * lexical_quality.anomaly_ratio,
        )
        text_quality = 1.0 - penalty

        warnings: list[str] = []
        if text_quality < self._policy.low_quality_warning_threshold:
            warnings.append("low text extraction quality")
        if character_error_count:
            warnings.append("invalid or mojibake text markers detected")
        if suspicious_spacing_ratio > self._policy.suspicious_spacing_warning_threshold:
            warnings.append("suspicious intra-word spacing detected")
        if duplicate_ratio > self._policy.duplicate_warning_threshold:
            warnings.append("high repeated text ratio")
        if lexical_quality.anomaly_count:
            warnings.append("language-aware lexical anomalies detected")

        metrics = TextQualityMetrics(
            element_count=len(snapshot),
            text_element_count=len(texts),
            character_count=len(joined),
            non_whitespace_character_count=non_whitespace,
            word_count=len(words),
            replacement_character_count=replacement_count,
            control_character_count=control_count,
            private_use_character_count=private_use_count,
            mojibake_marker_count=mojibake_count,
            suspicious_spacing_count=suspicious_spacing_count,
            duplicate_text_element_count=duplicate_count,
            character_error_ratio=character_error_ratio,
            suspicious_spacing_ratio=suspicious_spacing_ratio,
            duplicate_ratio=duplicate_ratio,
            lexical_anomaly_count=lexical_quality.anomaly_count,
            lexical_anomaly_ratio=lexical_quality.anomaly_ratio,
        )
        quality = DocumentQuality(
            text_quality=text_quality,
            duplicate_ratio=duplicate_ratio,
            garbage_ratio=garbage_ratio,
            warnings=tuple(warnings),
            lexical=LexicalQuality(
                version=lexical_quality.version,
                detected_language=lexical_quality.detected_language,
                token_count=lexical_quality.token_count,
                vietnamese_token_count=lexical_quality.vietnamese_token_count,
                english_token_count=lexical_quality.english_token_count,
                anomaly_count=lexical_quality.anomaly_count,
                anomaly_ratio=lexical_quality.anomaly_ratio,
                reason_counts=lexical_quality.reason_counts,
                element_anomaly_counts=lexical_quality.element_anomaly_counts,
                evidence=lexical_quality.evidence,
                evidence_truncated=lexical_quality.evidence_truncated,
            ),
            metrics={
                "text_quality_version": self.version,
                "text_quality_policy_version": self._policy.version,
                "text_character_error_ratio": character_error_ratio,
                "text_suspicious_spacing_ratio": suspicious_spacing_ratio,
                "text_duplicate_ratio": duplicate_ratio,
                "text_replacement_character_count": replacement_count,
                "text_control_character_count": control_count,
                "text_private_use_character_count": private_use_count,
                "text_mojibake_marker_count": mojibake_count,
                "text_suspicious_spacing_count": suspicious_spacing_count,
            },
        )
        return TextQualityReport(
            policy=self._policy,
            metrics=metrics,
            quality=quality,
            lexical_quality=lexical_quality,
        )


__all__ = [
    "TEXT_QUALITY_ESTIMATOR_VERSION",
    "TextQualityEstimator",
    "TextQualityMetrics",
    "TextQualityPolicy",
    "TextQualityReport",
]
