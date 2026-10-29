from __future__ import annotations

from enum import StrEnum
from math import isfinite
from typing import Annotated, Never, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
Label = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]
ContentHash = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^sha256:[0-9a-f]{64}$",
        min_length=71,
        max_length=71,
    ),
]
Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NormalizedCoordinate = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]


T = TypeVar("T")


class FrozenDict[T](dict[str, T]):
    """Small JSON-serializable immutable dict used inside frozen schemas."""

    @staticmethod
    def _immutable(*args: object, **kwargs: object) -> Never:
        raise TypeError("schema mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class FrozenList[T](list[T]):
    """Small JSON-serializable immutable list for nested JSON metadata."""

    @staticmethod
    def _immutable(*args: object, **kwargs: object) -> Never:
        raise TypeError("schema sequences are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable


def _freeze_json(item: object) -> object:
    """Validate one recursive JSON value and freeze mutable containers.

    ``object`` is intentional at this boundary. Python's mutable ``list``/``dict``
    invariance makes a recursive ``JsonValue`` annotation reject otherwise valid
    producers such as ``list[str]`` or ``dict[str, str]``. Runtime validation is
    authoritative here: unsupported Python objects and non-finite floats fail
    before metadata enters any immutable schema.
    """

    if item is None or isinstance(item, (str, bool, int)):
        return item
    if isinstance(item, float):
        if not isfinite(item):
            raise ValueError("JSON metadata cannot contain NaN or Infinity")
        return item
    if isinstance(item, dict):
        frozen: dict[str, object] = {}
        for key, value in item.items():
            if not isinstance(key, str):
                raise ValueError("JSON metadata object keys must be strings")  # noqa: TRY004 - Pydantic validator.
            frozen[key] = _freeze_json(value)
        return FrozenDict(frozen)
    if isinstance(item, list):
        return FrozenList(_freeze_json(value) for value in item)
    raise ValueError(f"JSON metadata contains unsupported value type {type(item).__name__}")


def _validate_and_freeze_json(value: dict[str, object]) -> dict[str, object]:
    return FrozenDict({key: _freeze_json(item) for key, item in value.items()})


def _freeze_mapping(value: dict[str, Confidence]) -> dict[str, Confidence]:
    return FrozenDict(value)


JsonObject = Annotated[dict[str, object], AfterValidator(_validate_and_freeze_json)]
ConfidenceMap = Annotated[dict[str, Confidence], AfterValidator(_freeze_mapping)]


class SchemaModel(BaseModel):
    """Immutable base model for source-understanding schemas."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        use_enum_values=False,
        validate_default=True,
    )


class StructureSource(StrEnum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    DERIVED = "DERIVED"


class StructureMode(StrEnum):
    UNKNOWN = "UNKNOWN"
    FLAT = "FLAT"
    LOCAL = "LOCAL"
    GROUPED = "GROUPED"
    HIERARCHICAL = "HIERARCHICAL"
    MIXED = "MIXED"


class ContextNode(SchemaModel):
    id: Identifier
    type: Label
    label: Label
    level: int | None = Field(default=None, ge=0)
    source: StructureSource
    confidence: Confidence
    parent_id: Identifier | None = None
    attributes: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_not_self_parent(self) -> ContextNode:
        if self.parent_id == self.id:
            raise ValueError("context node cannot be its own parent")
        return self


class ContextNodeRef(SchemaModel):
    id: Identifier
    type: Label | None = None
    label: Label | None = None
    source: StructureSource | None = None
    confidence: Confidence | None = None
