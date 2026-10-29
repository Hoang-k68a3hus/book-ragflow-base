from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import pairwise

from pydantic import Field

from rag.source_understanding.schemas.context import Confidence, JsonObject, SchemaModel
from rag.source_understanding.schemas.document import (
    LexicalAnomalyEvidence,
    LexicalAnomalyReason,
    LexicalLanguage,
)
from rag.source_understanding.schemas.element import Element

LEXICAL_QUALITY_SCORER_VERSION = "1"

_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)
_VIETNAMESE_MARKS = frozenset("ăâđêôơưĂÂĐÊÔƠƯ")
_TONE_MARKS = frozenset({"\u0300", "\u0301", "\u0303", "\u0309", "\u0323"})
_SHAPE_MARKS = frozenset({"\u0302", "\u0306", "\u031b"})
_VIETNAMESE_VOWELS = frozenset("aeiouy")
_ENGLISH_STOPWORDS = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "with"}
)
_ENGLISH_CONSONANT_RUN_RE = re.compile(r"[bcdfghjklmnpqrstvwxyz]{7,}", re.IGNORECASE)
_REPEATED_LETTER_RE = re.compile(r"([a-z])\1{3,}", re.IGNORECASE)


class LexicalQualityPolicy(SchemaModel):
    version: str = LEXICAL_QUALITY_SCORER_VERSION
    max_evidence: int = Field(default=100, ge=1, le=1000)
    minimum_language_tokens: int = Field(default=5, ge=1, le=100)


class LexicalQualityReport(SchemaModel):
    version: str = LEXICAL_QUALITY_SCORER_VERSION
    policy: LexicalQualityPolicy
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


class LexicalQualityScorer:
    """High-precision vi/en anomaly scoring; never rewrites source text."""

    version = LEXICAL_QUALITY_SCORER_VERSION

    def __init__(self, policy: LexicalQualityPolicy | None = None) -> None:
        self._policy = policy if policy is not None else LexicalQualityPolicy()

    def score(
        self,
        elements: Sequence[Element],
        *,
        language_hint: str | None = None,
    ) -> LexicalQualityReport:
        tokens = tuple(
            (element.id, token)
            for element in elements
            if element.text
            for token in _TOKEN_RE.findall(element.text)
        )
        detected = self._detect_language((token for _, token in tokens), language_hint)
        evidence: list[LexicalAnomalyEvidence] = []
        vi_count = 0
        en_count = 0
        anomaly_count = 0
        reason_counts: Counter[str] = Counter()
        element_anomaly_counts: Counter[str] = Counter()
        for element_id, token in tokens:
            token_language = self._token_language(token, detected)
            if token_language == LexicalLanguage.VIETNAMESE:
                vi_count += 1
                reason = self._vietnamese_anomaly(token)
            elif token_language == LexicalLanguage.ENGLISH:
                en_count += 1
                reason = self._english_anomaly(token, detected)
            else:
                reason = None
            if reason is None:
                continue
            anomaly_count += 1
            reason_counts[reason.value] += 1
            element_anomaly_counts[element_id] += 1
            if len(evidence) < self._policy.max_evidence:
                evidence.append(
                    LexicalAnomalyEvidence(
                        element_id=element_id,
                        token=token,
                        language=token_language,
                        reason=reason,
                    )
                )
        return LexicalQualityReport(
            policy=self._policy,
            detected_language=detected,
            token_count=len(tokens),
            vietnamese_token_count=vi_count,
            english_token_count=en_count,
            anomaly_count=anomaly_count,
            anomaly_ratio=anomaly_count / max(1, len(tokens)),
            reason_counts=dict(sorted(reason_counts.items())),
            element_anomaly_counts=dict(sorted(element_anomaly_counts.items())),
            evidence=tuple(evidence),
            evidence_truncated=anomaly_count > len(evidence),
        )

    def _detect_language(self, tokens: Iterable[str], hint: str | None) -> LexicalLanguage:
        normalized_hint = (hint or "").strip().casefold().replace("_", "-")
        if normalized_hint.startswith("vi") or "vietnam" in normalized_hint:
            return LexicalLanguage.VIETNAMESE
        if normalized_hint.startswith("en") or "english" in normalized_hint:
            return LexicalLanguage.ENGLISH
        snapshot = tuple(tokens)
        vi = sum(self._has_vietnamese_signal(token) for token in snapshot)
        en = sum(token.casefold() in _ENGLISH_STOPWORDS for token in snapshot)
        threshold = self._policy.minimum_language_tokens
        if vi >= threshold and en >= threshold:
            return LexicalLanguage.MIXED
        if vi >= threshold:
            return LexicalLanguage.VIETNAMESE
        if en >= threshold:
            return LexicalLanguage.ENGLISH
        return LexicalLanguage.UNKNOWN

    @classmethod
    def _token_language(cls, token: str, document_language: LexicalLanguage) -> LexicalLanguage:
        if any(char in _VIETNAMESE_MARKS for char in token):
            return LexicalLanguage.VIETNAMESE
        if document_language == LexicalLanguage.ENGLISH:
            return LexicalLanguage.ENGLISH
        if cls._has_vietnamese_signal(token):
            return LexicalLanguage.VIETNAMESE
        if token.isascii() and token.isalpha():
            return LexicalLanguage.ENGLISH
        if document_language in {LexicalLanguage.VIETNAMESE, LexicalLanguage.ENGLISH}:
            return document_language
        return LexicalLanguage.UNKNOWN

    @staticmethod
    def _has_vietnamese_signal(token: str) -> bool:
        if any(char in _VIETNAMESE_MARKS for char in token):
            return True
        decomposed = unicodedata.normalize("NFD", token)
        return any(char in _TONE_MARKS for char in decomposed)

    @classmethod
    def _vietnamese_anomaly(cls, token: str) -> LexicalAnomalyReason | None:
        decomposed = unicodedata.normalize("NFD", token.casefold())
        tone_positions: list[int] = []
        shaped_letters: list[str] = []
        current = -1
        for char in decomposed:
            if char.isalpha() and not unicodedata.combining(char):
                shaped_letters.append(char)
                current += 1
            elif char in _SHAPE_MARKS and current >= 0:
                shaped_letters[current] = unicodedata.normalize("NFC", shaped_letters[current] + char)
            elif char in _TONE_MARKS and current >= 0:
                tone_positions.append(current)
        if len(tone_positions) > 1:
            return LexicalAnomalyReason.MULTIPLE_VIETNAMESE_TONES
        vowel_positions = [index for index, char in enumerate(shaped_letters) if cls._base_letter(char) in _VIETNAMESE_VOWELS]
        if not vowel_positions:
            return None
        nucleus = "".join(shaped_letters[index] for index in vowel_positions)
        if any(left == right for left, right in pairwise(nucleus)):
            return LexicalAnomalyReason.INVALID_VIETNAMESE_VOWEL_NUCLEUS
        if tone_positions and nucleus == "ưi" and tone_positions[0] != vowel_positions[0]:
            return LexicalAnomalyReason.MISPLACED_VIETNAMESE_TONE
        return None

    @staticmethod
    def _base_letter(char: str) -> str:
        return "".join(part for part in unicodedata.normalize("NFD", char) if not unicodedata.combining(part))

    @staticmethod
    def _english_anomaly(token: str, document_language: LexicalLanguage) -> LexicalAnomalyReason | None:
        if document_language == LexicalLanguage.ENGLISH and not token.isascii():
            return LexicalAnomalyReason.ENGLISH_UNEXPECTED_DIACRITIC
        if token.isupper():
            return None
        if _REPEATED_LETTER_RE.search(token):
            return LexicalAnomalyReason.ENGLISH_REPEATED_LETTER
        if _ENGLISH_CONSONANT_RUN_RE.search(token):
            return LexicalAnomalyReason.ENGLISH_CONSONANT_RUN
        return None


__all__ = [
    "LEXICAL_QUALITY_SCORER_VERSION",
    "LexicalAnomalyEvidence",
    "LexicalAnomalyReason",
    "LexicalLanguage",
    "LexicalQualityPolicy",
    "LexicalQualityReport",
    "LexicalQualityScorer",
]
