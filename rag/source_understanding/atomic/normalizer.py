from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import Identifier, SchemaModel
from rag.source_understanding.schemas.element import (
    Element,
    ElementConfidence,
    ElementType,
    Provenance,
    RawElement,
    TransformationRecord,
)

ELEMENT_NORMALIZER_VERSION = "3"
ELEMENT_NORMALIZER_POLICY_VERSION = "1"


class ElementNormalizationError(ValueError):
    """Raw adapter output cannot be normalized without losing source identity."""


class UnicodeNormalizationForm(StrEnum):
    NONE = "NONE"
    NFC = "NFC"
    NFKC = "NFKC"


class ElementNormalizationPolicy(SchemaModel):
    version: str = Field(
        default=ELEMENT_NORMALIZER_POLICY_VERSION,
        min_length=1,
        max_length=128,
    )
    normalize_line_endings: bool = True
    unicode_form: UnicodeNormalizationForm = UnicodeNormalizationForm.NFC


class ElementNormalizationResult(SchemaModel):
    version: str = ELEMENT_NORMALIZER_VERSION
    document_id: Identifier
    raw_element_count: int = Field(ge=1)
    element_count: int = Field(ge=1)
    policy: ElementNormalizationPolicy
    elements: tuple[Element, ...] = Field(min_length=1)
    transformed_element_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    unknown_type_hint_element_ids: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_result(self) -> ElementNormalizationResult:
        if self.raw_element_count != self.element_count:
            raise ValueError("normalization must preserve raw element cardinality")
        ids = [element.id for element in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("normalized element ids must be unique")
        known = set(ids)
        for name, refs in (
            ("transformed_element_ids", self.transformed_element_ids),
            ("unknown_type_hint_element_ids", self.unknown_type_hint_element_ids),
        ):
            if len(refs) != len(set(refs)):
                raise ValueError(f"{name} must be unique")
            missing = set(refs) - known
            if missing:
                raise ValueError(f"{name} references unknown elements: {sorted(missing)}")
        return self


class ElementNormalizer:
    """Convert source-near RawElements into conservative canonical Elements."""

    version = ELEMENT_NORMALIZER_VERSION

    def __init__(self, policy: ElementNormalizationPolicy | None = None) -> None:
        self._policy = policy if policy is not None else ElementNormalizationPolicy()

    def normalize(
        self,
        raw_elements: Sequence[RawElement],
        *,
        document_id: str,
    ) -> ElementNormalizationResult:
        snapshot = tuple(raw_elements)
        self._validate_inputs(snapshot, document_id)

        elements: list[Element] = []
        transformed: list[str] = []
        unknown_hints: list[str] = []

        for raw in snapshot:
            element_id = self._element_id(document_id, raw)
            element_type, unknown_hint = self._resolve_type(raw.type_hint)
            normalized_text, transformations = self._normalize_text(raw.text)
            provenance = self._with_transformations(raw.provenance, transformations)

            element = Element(
                id=element_id,
                type=element_type,
                source_type_hint=raw.type_hint,
                order=raw.order,
                raw_text=raw.text,
                normalized_text=normalized_text,
                location=raw.location,
                style=raw.style,
                attributes=raw.attributes,
                confidence=ElementConfidence(),
                provenance=provenance,
                exclude_from_retrieval=False,
            )
            elements.append(element)
            if transformations:
                transformed.append(element_id)
            if unknown_hint:
                unknown_hints.append(element_id)

        return ElementNormalizationResult(
            document_id=document_id,
            raw_element_count=len(snapshot),
            element_count=len(elements),
            policy=self._policy,
            elements=tuple(elements),
            transformed_element_ids=tuple(transformed),
            unknown_type_hint_element_ids=tuple(unknown_hints),
        )

    def _normalize_text(
        self,
        text: str | None,
    ) -> tuple[str | None, tuple[TransformationRecord, ...]]:
        if text is None:
            return None, ()

        value = text
        transformations: list[TransformationRecord] = []

        if self._policy.normalize_line_endings:
            normalized = value.replace("\r\n", "\n").replace("\r", "\n")
            if normalized != value:
                transformations.append(
                    TransformationRecord(
                        operation="normalize_line_endings",
                        metadata={
                            "crlf_count": value.count("\r\n"),
                            "standalone_cr_count": value.count("\r") - value.count("\r\n"),
                        },
                    )
                )
                value = normalized

        if self._policy.unicode_form != UnicodeNormalizationForm.NONE:
            normalized = unicodedata.normalize(self._policy.unicode_form.value, value)
            if normalized != value:
                transformations.append(
                    TransformationRecord(
                        operation="normalize_unicode",
                        metadata={"form": self._policy.unicode_form.value},
                    )
                )
                value = normalized

        return value, tuple(transformations)

    @staticmethod
    def _resolve_type(type_hint: str | None) -> tuple[ElementType, bool]:
        if type_hint is None or not type_hint.strip():
            return ElementType.UNKNOWN, False
        normalized = type_hint.strip().upper().replace("-", "_").replace(" ", "_")
        try:
            return ElementType(normalized), False
        except ValueError:
            return ElementType.UNKNOWN, True

    @staticmethod
    def _with_transformations(
        provenance: Provenance,
        transformations: tuple[TransformationRecord, ...],
    ) -> Provenance:
        if not transformations:
            return provenance
        data = provenance.model_dump(mode="python")
        data["transformations"] = (*provenance.transformations, *transformations)
        return Provenance.model_validate(data)

    @staticmethod
    def _element_id(document_id: str, raw: RawElement) -> str:
        # Element identity belongs to the source-near observation, not to the
        # software version or provenance bookkeeping that happened to extract it.
        # Location coordinates/ranges identify the observation; SourceLocation.source
        # classifies how that location was obtained and therefore must not churn
        # downstream references when provenance becomes more precise.
        payload = {
            "document_id": document_id,
            "source_observation": {
                "text": raw.text,
                "type_hint": raw.type_hint,
                "order": raw.order,
                "location": (raw.location.model_dump(mode="json", exclude={"source"}) if raw.location is not None else None),
                "style": (raw.style.model_dump(mode="json") if raw.style is not None else None),
                "attributes": raw.attributes,
                "source": raw.provenance.source.value,
            },
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        return f"el_{digest}"

    @staticmethod
    def _validate_inputs(raw_elements: tuple[RawElement, ...], document_id: str) -> None:
        if not isinstance(document_id, str) or not document_id or document_id.strip() != document_id:
            raise ElementNormalizationError("document_id must be a trimmed non-blank string")
        if not raw_elements:
            raise ElementNormalizationError("cannot normalize an empty RawElement sequence")

        orders = [raw.order for raw in raw_elements]
        if len(orders) != len(set(orders)):
            raise ElementNormalizationError("RawElements must have unique order values")
        if orders != sorted(orders):
            raise ElementNormalizationError("RawElements must arrive in ascending source order; normalizer will not reorder them")
