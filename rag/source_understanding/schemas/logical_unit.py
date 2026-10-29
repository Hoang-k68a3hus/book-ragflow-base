from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .context import Confidence, Identifier, JsonObject, Label, SchemaModel, StructureSource


class LogicalUnitType(StrEnum):
    """Content-integrity and structural unit types, not semantic roles."""

    TEXT_BLOCK = "TEXT_BLOCK"
    SECTION = "SECTION"
    TOPIC_GROUP = "TOPIC_GROUP"
    QA_PAIR = "QA_PAIR"
    DIALOGUE_SEGMENT = "DIALOGUE_SEGMENT"
    CODE_BLOCK = "CODE_BLOCK"
    TABLE_BLOCK = "TABLE_BLOCK"
    LOG_WINDOW = "LOG_WINDOW"
    KEY_VALUE_GROUP = "KEY_VALUE_GROUP"
    LIST_GROUP = "LIST_GROUP"
    UNKNOWN_GROUP = "UNKNOWN_GROUP"


class LogicalUnit(SchemaModel):
    id: Identifier
    type: LogicalUnitType
    element_ids: tuple[Identifier, ...] = Field(min_length=1)
    region_id: Identifier | None = None
    context_node_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    label: Label | None = None
    source: StructureSource
    confidence: Confidence
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_refs(self) -> LogicalUnit:
        if len(self.element_ids) != len(set(self.element_ids)):
            raise ValueError("logical unit element_ids must be unique")
        if len(self.context_node_ids) != len(set(self.context_node_ids)):
            raise ValueError("logical unit context_node_ids must be unique")
        return self
