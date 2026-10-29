from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .context import Confidence, Identifier, JsonObject, SchemaModel, StructureSource


class RelationLayer(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    SEMANTIC = "SEMANTIC"


class RelationType(StrEnum):
    NEXT = "NEXT"
    PARENT_OF = "PARENT_OF"
    CONTINUES = "CONTINUES"
    QUESTION_ANSWER = "QUESTION_ANSWER"
    CAPTION_OF = "CAPTION_OF"
    FOOTNOTE_OF = "FOOTNOTE_OF"
    EXPLAINS = "EXPLAINS"
    REFERS_TO = "REFERS_TO"
    SAME_TOPIC = "SAME_TOPIC"
    PREREQUISITE_OF = "PREREQUISITE_OF"
    PART_OF = "PART_OF"
    SEQUENCE_BEFORE = "SEQUENCE_BEFORE"
    DUPLICATE_OF = "DUPLICATE_OF"


_STRUCTURAL_TYPES = frozenset(
    {
        RelationType.NEXT,
        RelationType.PARENT_OF,
        RelationType.CONTINUES,
        RelationType.QUESTION_ANSWER,
        RelationType.CAPTION_OF,
        RelationType.FOOTNOTE_OF,
        RelationType.PART_OF,
        RelationType.SEQUENCE_BEFORE,
        RelationType.DUPLICATE_OF,
    }
)
_SEMANTIC_TYPES = frozenset(
    {
        RelationType.EXPLAINS,
        RelationType.REFERS_TO,
        RelationType.SAME_TOPIC,
        RelationType.PREREQUISITE_OF,
    }
)


class Relation(SchemaModel):
    id: Identifier
    layer: RelationLayer
    type: RelationType
    source_id: Identifier
    target_id: Identifier
    confidence: Confidence
    source: StructureSource
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_relation(self) -> Relation:
        if self.source_id == self.target_id:
            raise ValueError("relation cannot target its own source")
        expected_layer = RelationLayer.STRUCTURAL if self.type in _STRUCTURAL_TYPES else RelationLayer.SEMANTIC
        if self.type not in _STRUCTURAL_TYPES and self.type not in _SEMANTIC_TYPES:
            raise ValueError(f"relation type {self.type!s} has no registered layer")
        if self.layer != expected_layer:
            raise ValueError(f"relation type {self.type.value} belongs to {expected_layer.value}, not {self.layer.value}")
        return self
