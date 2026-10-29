from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field, model_validator

from rag.source_understanding.schemas.context import JsonObject, SchemaModel
from rag.source_understanding.schemas.document import Asset, DocumentMetadata, ProcessingManifest
from rag.source_understanding.schemas.element import RawElement

SOURCE_ADAPTER_PROTOCOL_VERSION = "1"


class AdapterError(ValueError):
    """A source package cannot be adapted without violating source-near invariants."""


class AdapterDiagnosticLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"


class AdapterDiagnostic(SchemaModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4096)
    level: AdapterDiagnosticLevel = AdapterDiagnosticLevel.WARNING
    affects_structural_completeness: bool = False
    part: str | None = Field(default=None, max_length=4096)
    metadata: JsonObject = Field(default_factory=dict)


class SourceAdapterResult(SchemaModel):
    """Source-near adapter output before canonical element normalization.

    ``content_hash`` always hashes the exact input byte stream.  RawElements keep
    source observations only; structure/semantic inference remains downstream.
    """

    protocol_version: str = SOURCE_ADAPTER_PROTOCOL_VERSION
    adapter_name: str = Field(min_length=1, max_length=256)
    adapter_version: str = Field(min_length=1, max_length=128)
    media_type: str = Field(min_length=1, max_length=256)
    source_name: str | None = Field(default=None, max_length=4096)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71)
    raw_elements: tuple[RawElement, ...] = Field(default_factory=tuple)
    assets: tuple[Asset, ...] = Field(default_factory=tuple)
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    diagnostics: tuple[AdapterDiagnostic, ...] = Field(default_factory=tuple)
    configuration: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> SourceAdapterResult:
        orders = [element.order for element in self.raw_elements]
        if len(orders) != len(set(orders)):
            raise ValueError("adapter raw_elements must have unique order values")
        if orders != sorted(orders):
            raise ValueError("adapter raw_elements must follow ascending source order")
        asset_ids = [asset.id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("adapter assets must have unique ids")
        return self

    def processing_manifest(self, *, processed_at: datetime) -> ProcessingManifest:
        if processed_at.tzinfo is None or processed_at.utcoffset() is None:
            raise AdapterError("processed_at must be timezone-aware")
        diagnostic_counts: dict[str, int] = {}
        structural_issue_counts: dict[str, int] = {}
        warning_count = 0
        for diagnostic in self.diagnostics:
            diagnostic_counts[diagnostic.code] = diagnostic_counts.get(diagnostic.code, 0) + 1
            if diagnostic.level == AdapterDiagnosticLevel.WARNING:
                warning_count += 1
            if diagnostic.affects_structural_completeness:
                structural_issue_counts[diagnostic.code] = structural_issue_counts.get(diagnostic.code, 0) + 1
        return ProcessingManifest(
            adapter_name=self.adapter_name,
            adapter_version=self.adapter_version,
            processed_at=processed_at,
            configuration={
                "source_adapter": {
                    "protocol_version": self.protocol_version,
                    "media_type": self.media_type,
                    "source_name": self.source_name,
                    "policy": self.configuration,
                    "raw_element_count": len(self.raw_elements),
                    "asset_count": len(self.assets),
                    "diagnostic_counts": dict(sorted(diagnostic_counts.items())),
                    "warning_count": warning_count,
                    "structural_issue_count": sum(structural_issue_counts.values()),
                    "structural_issue_counts": dict(sorted(structural_issue_counts.items())),
                }
            },
        )

    @classmethod
    def hash_bytes(cls, data: bytes) -> str:
        return "sha256:" + hashlib.sha256(data).hexdigest()


@runtime_checkable
class SourceAdapter(Protocol):
    name: str
    version: str
    media_types: tuple[str, ...]
    extensions: tuple[str, ...]

    def adapt(
        self,
        data: bytes,
        *,
        source_name: str | None = None,
    ) -> SourceAdapterResult: ...


def validate_adapter(adapter: SourceAdapter) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    name = getattr(adapter, "name", None)
    version = getattr(adapter, "version", None)
    media_types = getattr(adapter, "media_types", None)
    extensions = getattr(adapter, "extensions", None)
    adapt = getattr(adapter, "adapt", None)
    if not isinstance(name, str) or not name.strip():
        raise AdapterError("source adapter must expose a non-blank name")
    if not isinstance(version, str) or not version.strip():
        raise AdapterError("source adapter must expose a non-blank version")
    if not isinstance(media_types, tuple) or not media_types:
        raise AdapterError("source adapter must expose a non-empty media_types tuple")
    if any(not isinstance(media_type, str) or not media_type.strip() or media_type != media_type.strip() for media_type in media_types):
        raise AdapterError("source adapter media_types must contain trimmed non-blank strings")
    if len(media_types) != len(set(media_types)):
        raise AdapterError("source adapter media_types must be unique")
    if not isinstance(extensions, tuple):
        raise AdapterError("source adapter must expose an extensions tuple")
    if any(not isinstance(extension, str) or not extension.startswith(".") or extension.lower() != extension or extension.strip() != extension or len(extension) < 2 for extension in extensions):
        raise AdapterError("source adapter extensions must be lowercase trimmed dot-prefixed strings")
    if len(extensions) != len(set(extensions)):
        raise AdapterError("source adapter extensions must be unique")
    if not callable(adapt):
        raise AdapterError("source adapter must expose adapt(data, source_name=...)")
    return name.strip(), version.strip(), media_types, extensions
