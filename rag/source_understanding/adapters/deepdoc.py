"""Bridge typed DeepDOC observations into source-understanding RawElements."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from deepdoc.parser.source_observation import (
    DeepDocBoundingBox,
    DeepDocDiagnostic,
    DeepDocDiagnosticLevel,
    DeepDocExtractionSource,
    DeepDocJsonValue,
    DeepDocObservation,
    DeepDocObservationKind,
    DeepDocParseResult,
    DeepDocSourceLocation,
    DeepDocStyle,
)
from rag.source_understanding.schemas.context import StructureSource
from rag.source_understanding.schemas.document import DocumentMetadata
from rag.source_understanding.schemas.element import (
    BoundingBox,
    Provenance,
    RawElement,
    SourceLocation,
    StyleInfo,
)
from rag.source_understanding.source_attributes import SOURCE_ANCHOR_ATTRIBUTE, SOURCE_ZONE_ATTRIBUTE

from .base import (
    AdapterDiagnostic,
    AdapterDiagnosticLevel,
    AdapterError,
    SourceAdapterResult,
)

DEEPDOC_SOURCE_ADAPTER_VERSION = "1"
DEEPDOC_PDF_OBSERVATION_BUILDER_VERSION = "1"
DEEPDOC_PDF_MEDIA_TYPE = "application/pdf"


_LAYOUT_TYPE_HINTS = {
    "title": "TITLE",
    "heading": "HEADING",
    "text": "PARAGRAPH",
    "paragraph": "PARAGRAPH",
    "list": "LIST",
    "list item": "LIST_ITEM",
    "table": "TABLE",
    "figure": "FIGURE",
    "figure caption": "CAPTION",
    "table caption": "CAPTION",
    "caption": "CAPTION",
    "equation": "FORMULA",
    "formula": "FORMULA",
    "header": "HEADER",
    "footer": "FOOTER",
    "page number": "PAGE_NUMBER",
}


class DeepDocSourceAdapter:
    """Adapt one already-extracted DeepDOC result without rerunning DeepDOC.

    The parse result is bound at construction time so this class still conforms
    to the existing ``SourceAdapter.adapt(bytes, source_name=...)`` boundary. The
    exact input bytes remain the authority for ``content_hash``.
    """

    name = "deepdoc-observation-bridge"
    version = DEEPDOC_SOURCE_ADAPTER_VERSION
    media_types = (DEEPDOC_PDF_MEDIA_TYPE,)
    extensions = (".pdf",)

    def __init__(self, parsed: DeepDocParseResult) -> None:
        if parsed.media_type not in self.media_types:
            raise AdapterError(f"DeepDOC media type {parsed.media_type!r} is not supported by this adapter")
        self._parsed = parsed

    def adapt(
        self,
        data: bytes,
        *,
        source_name: str | None = None,
    ) -> SourceAdapterResult:
        if not isinstance(data, bytes):
            raise AdapterError("DeepDOC source adapter requires exact source bytes")
        if not data:
            raise AdapterError("DeepDOC source adapter cannot adapt an empty source")

        diagnostics = [self._adapt_diagnostic(item) for item in self._parsed.diagnostics]
        raw_elements: list[RawElement] = []
        for observation in self._parsed.observations:
            location, location_diagnostics = self._adapt_location(observation)
            diagnostics.extend(location_diagnostics)
            raw_elements.append(
                RawElement(
                    text=observation.text,
                    type_hint=self._type_hint(observation),
                    order=observation.order,
                    location=location,
                    style=self._style(observation.style),
                    attributes=self._attributes(observation),
                    provenance=Provenance(
                        source=self._structure_source(observation.extraction_source),
                        extractor=self._parsed.parser_name,
                        extractor_version=self._parsed.parser_version,
                        metadata={
                            "deepdoc_source_id": observation.source_id,
                            "deepdoc_extraction_source": observation.extraction_source.value,
                            "observation_contract": DEEPDOC_SOURCE_ADAPTER_VERSION,
                        },
                    ),
                )
            )

        return SourceAdapterResult(
            adapter_name=self.name,
            adapter_version=self.version,
            media_type=self._parsed.media_type,
            source_name=source_name,
            content_hash=SourceAdapterResult.hash_bytes(data),
            raw_elements=tuple(raw_elements),
            metadata=DocumentMetadata(
                title=_source_title(source_name),
                source_name=source_name,
                attributes={
                    "deepdoc": {
                        "parser_name": self._parsed.parser_name,
                        "parser_version": self._parsed.parser_version,
                        "observation_count": len(raw_elements),
                        "outline_item_count": len(self._parsed.outline),
                        "attributes": _thaw_json_object(self._parsed.attributes),
                    }
                },
            ),
            diagnostics=tuple(diagnostics),
            configuration={
                "deepdoc_observation_contract_version": DEEPDOC_SOURCE_ADAPTER_VERSION,
                "deepdoc_parser_name": self._parsed.parser_name,
                "deepdoc_parser_version": self._parsed.parser_version,
                "outline": [
                    {
                        "title": item.title,
                        "level": item.level,
                        "page": item.page,
                    }
                    for item in self._parsed.outline
                ],
            },
        )

    @classmethod
    def _adapt_location(
        cls,
        observation: DeepDocObservation,
    ) -> tuple[SourceLocation | None, list[AdapterDiagnostic]]:
        if not observation.locations:
            return None, []

        primary = observation.locations[0]
        source = cls._structure_source(observation.extraction_source)
        diagnostics: list[AdapterDiagnostic] = []
        bbox = None
        if primary.bbox is not None:
            if cls._bbox_is_inside_page(primary):
                bbox = BoundingBox(
                    x0=primary.bbox.x0 / primary.page_width,
                    y0=primary.bbox.y0 / primary.page_height,
                    x1=primary.bbox.x1 / primary.page_width,
                    y1=primary.bbox.y1 / primary.page_height,
                )
            else:
                diagnostics.append(
                    AdapterDiagnostic(
                        code="DEEPDOC_BBOX_OUTSIDE_PAGE",
                        message=("DeepDOC reported a bounding box outside its page; the adapter preserved page identity and raw geometry without inventing normalized coordinates"),
                        level=AdapterDiagnosticLevel.WARNING,
                        part=observation.source_id,
                        metadata={
                            "page": primary.page,
                            "bbox": cls._bbox_payload(primary.bbox),
                            "page_width": primary.page_width,
                            "page_height": primary.page_height,
                        },
                    )
                )

        return SourceLocation(source=source, page=primary.page, bbox=bbox), diagnostics

    @staticmethod
    def _bbox_is_inside_page(location: DeepDocSourceLocation) -> bool:
        bbox = location.bbox
        return bool(bbox is not None and bbox.x0 >= 0 and bbox.y0 >= 0 and bbox.x1 <= location.page_width and bbox.y1 <= location.page_height)

    @classmethod
    def _attributes(cls, observation: DeepDocObservation) -> dict[str, object]:
        locations = []
        for location in observation.locations:
            payload: dict[str, object] = {
                "page": location.page,
                "page_width": location.page_width,
                "page_height": location.page_height,
            }
            if location.bbox is not None:
                payload["bbox"] = cls._bbox_payload(location.bbox)
            locations.append(payload)

        attributes = {
            SOURCE_ANCHOR_ATTRIBUTE: {
                "kind": "deepdoc_observation",
                "id": observation.source_id,
            },
            "deepdoc_observation": {
                "kind": observation.kind.value,
                "layout_type": observation.layout_type,
                "extraction_source": observation.extraction_source.value,
                "locations": locations,
                "attributes": _thaw_json_object(observation.attributes),
            },
        }
        zone = observation.attributes.get(SOURCE_ZONE_ATTRIBUTE)
        if isinstance(zone, str) and zone.strip():
            attributes[SOURCE_ZONE_ATTRIBUTE] = zone
        return attributes

    @staticmethod
    def _bbox_payload(bbox: DeepDocBoundingBox) -> list[float]:
        return [bbox.x0, bbox.y0, bbox.x1, bbox.y1]

    @staticmethod
    def _style(style: DeepDocStyle | None) -> StyleInfo | None:
        if style is None:
            return None
        return StyleInfo(
            font_name=style.font_name,
            font_size=style.font_size,
            bold=style.bold,
            italic=style.italic,
            color=style.color,
            alignment=style.alignment,
            indentation=style.indentation,
            attributes=_thaw_json_object(style.attributes),
        )

    @staticmethod
    def _type_hint(observation: DeepDocObservation) -> str:
        if observation.type_hint:
            return observation.type_hint
        if observation.kind == DeepDocObservationKind.TABLE:
            return "TABLE"
        if observation.kind == DeepDocObservationKind.FIGURE:
            return "FIGURE"
        layout_type = (observation.layout_type or "").strip().casefold()
        return _LAYOUT_TYPE_HINTS.get(layout_type, "PARAGRAPH")

    @staticmethod
    def _structure_source(source: DeepDocExtractionSource) -> StructureSource:
        if source == DeepDocExtractionSource.NATIVE:
            return StructureSource.EXPLICIT
        return StructureSource.DERIVED

    @staticmethod
    def _adapt_diagnostic(item: DeepDocDiagnostic) -> AdapterDiagnostic:
        level = AdapterDiagnosticLevel.INFO if item.level == DeepDocDiagnosticLevel.INFO else AdapterDiagnosticLevel.WARNING
        return AdapterDiagnostic(
            code=item.code,
            message=item.message,
            level=level,
            affects_structural_completeness=item.affects_structural_completeness,
            metadata=_thaw_json_object(item.metadata),
        )


class DeepDocPdfObservationBuilder:
    """Build typed observations from the source-near state of RAGFlow's PDF parser."""

    version = DEEPDOC_PDF_OBSERVATION_BUILDER_VERSION

    def build(
        self,
        parser: object,
        *,
        tables: Iterable[object] = (),
        zoomin: float = 3.0,
    ) -> DeepDocParseResult:
        if not math.isfinite(zoomin) or zoomin <= 0:
            raise AdapterError("DeepDOC PDF zoom must be finite and positive")

        page_geometry = self._page_geometry(parser, zoomin)
        cumulative_heights = tuple(float(value) for value in getattr(parser, "page_cum_height", ()))
        page_from = int(getattr(parser, "page_from", 0) or 0)
        diagnostics: list[DeepDocDiagnostic] = []
        candidates: list[DeepDocObservation] = []

        boxes = getattr(parser, "boxes", ())
        if not isinstance(boxes, Sequence):
            raise AdapterError("DeepDOC PDF parser boxes must be an ordered sequence")

        for index, box in enumerate(boxes):
            if not isinstance(box, Mapping):
                diagnostics.append(
                    self._diagnostic(
                        "DEEPDOC_PDF_INVALID_BOX",
                        f"DeepDOC PDF box {index} is not a mapping and was not projected",
                    )
                )
                continue
            text = box.get("text")
            if text is not None and not isinstance(text, str):
                text = str(text)
            if text is None or not text.strip():
                diagnostics.append(
                    self._diagnostic(
                        "DEEPDOC_PDF_EMPTY_BOX",
                        f"DeepDOC PDF box {index} has no visible text and was not projected",
                    )
                )
                continue

            location = self._box_location(
                box,
                page_geometry=page_geometry,
                cumulative_heights=cumulative_heights,
                page_from=page_from,
            )
            layout_type = self._trimmed_string(box.get("layout_type"))
            global_page = location.page if location is not None else 0
            candidates.append(
                DeepDocObservation(
                    source_id=f"pdf:text:{global_page}:{index}",
                    order=index,
                    kind=DeepDocObservationKind.TEXT,
                    text=text,
                    extraction_source=DeepDocExtractionSource.DERIVED,
                    type_hint=_LAYOUT_TYPE_HINTS.get((layout_type or "").casefold(), "PARAGRAPH"),
                    layout_type=layout_type,
                    locations=(location,) if location is not None else (),
                    style=self._box_style(box),
                    attributes=self._box_attributes(box),
                )
            )

        table_offset = len(candidates)
        for index, table in enumerate(tables):
            observation = self._table_observation(
                table,
                index=index,
                initial_order=table_offset + index,
                page_geometry=page_geometry,
            )
            if observation is None:
                diagnostics.append(
                    self._diagnostic(
                        "DEEPDOC_PDF_INVALID_TABLE",
                        f"DeepDOC PDF table {index} could not be projected",
                        affects_structural_completeness=True,
                    )
                )
                continue
            candidates.append(observation)

        ordered = sorted(candidates, key=self._reading_order_key)
        observations = tuple(replace(observation, order=order) for order, observation in enumerate(ordered))
        if not observations:
            diagnostics.append(
                self._diagnostic(
                    "DEEPDOC_PDF_NO_OBSERVATIONS",
                    "DeepDOC PDF extraction produced no source observations",
                    affects_structural_completeness=True,
                )
            )

        return DeepDocParseResult(
            parser_name="ragflow-deepdoc-pdf",
            parser_version=self.version,
            media_type=DEEPDOC_PDF_MEDIA_TYPE,
            observations=observations,
            diagnostics=tuple(diagnostics),
            attributes={
                "page_count": len(page_geometry),
                "page_from": page_from,
                "zoomin": zoomin,
                "reading_order": "page-y-x",
                "text_and_location_provenance": "derived",
            },
        )

    @staticmethod
    def _page_geometry(
        parser: object,
        zoomin: float,
    ) -> dict[int, tuple[int, float, float]]:
        images = tuple(getattr(parser, "page_images", ()) or ())
        page_from = int(getattr(parser, "page_from", 0) or 0)
        output: dict[int, tuple[int, float, float]] = {}
        for local_index, image in enumerate(images):
            size = getattr(image, "size", None)
            if not isinstance(size, (tuple, list)) or len(size) != 2 or float(size[0]) <= 0 or float(size[1]) <= 0:
                raise AdapterError(f"DeepDOC PDF page {local_index + 1} has invalid rendered dimensions")
            global_page = page_from + local_index + 1
            output[global_page] = (
                local_index,
                float(size[0]) / zoomin,
                float(size[1]) / zoomin,
            )
        return output

    @classmethod
    def _box_location(
        cls,
        box: Mapping[str, object],
        *,
        page_geometry: Mapping[int, tuple[int, float, float]],
        cumulative_heights: tuple[float, ...],
        page_from: int,
    ) -> DeepDocSourceLocation | None:
        try:
            raw_page = int(box["page_number"])
        except (KeyError, TypeError, ValueError):
            return None

        if raw_page in page_geometry:
            global_page = raw_page
        else:
            global_page = page_from + raw_page
        geometry = page_geometry.get(global_page)
        if geometry is None:
            return None
        local_index, page_width, page_height = geometry

        try:
            x0 = float(box["x0"])
            x1 = float(box["x1"])
            cumulative_top = cumulative_heights[local_index] if local_index < len(cumulative_heights) else 0.0
            y0 = float(box["top"]) - cumulative_top
            y1 = float(box["bottom"]) - cumulative_top
            bbox = DeepDocBoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)
        except (KeyError, TypeError, ValueError, IndexError):
            bbox = None

        return DeepDocSourceLocation(
            page=global_page,
            page_width=page_width,
            page_height=page_height,
            bbox=bbox,
        )

    @classmethod
    def _table_observation(
        cls,
        table: object,
        *,
        index: int,
        initial_order: int,
        page_geometry: Mapping[int, tuple[int, float, float]],
    ) -> DeepDocObservation | None:
        if not isinstance(table, (tuple, list)) or len(table) != 2:
            return None
        payload, positions = table
        if not isinstance(payload, (tuple, list)) or len(payload) != 2:
            return None
        _image, rows = payload
        if isinstance(rows, str):
            text = rows
        elif isinstance(rows, Sequence):
            text = "\n".join(str(row) for row in rows)
        else:
            return None
        if not text.strip():
            return None

        locations: list[DeepDocSourceLocation] = []
        if isinstance(positions, Sequence):
            for position in positions:
                if not isinstance(position, (tuple, list)) or len(position) != 5:
                    continue
                try:
                    global_page = int(position[0]) + 1
                    geometry = page_geometry.get(global_page)
                    if geometry is None:
                        continue
                    _, page_width, page_height = geometry
                    bbox = DeepDocBoundingBox(
                        x0=float(position[1]),
                        y0=float(position[3]),
                        x1=float(position[2]),
                        y1=float(position[4]),
                    )
                    locations.append(
                        DeepDocSourceLocation(
                            page=global_page,
                            page_width=page_width,
                            page_height=page_height,
                            bbox=bbox,
                        )
                    )
                except (TypeError, ValueError):
                    continue

        first_page = locations[0].page if locations else 0
        return DeepDocObservation(
            source_id=f"pdf:table:{first_page}:{index}",
            order=initial_order,
            kind=DeepDocObservationKind.TABLE,
            text=text,
            extraction_source=DeepDocExtractionSource.DERIVED,
            type_hint="TABLE",
            layout_type="table",
            locations=tuple(locations),
            attributes={
                "representation": "html" if isinstance(rows, str) else "rows",
                "spans_multiple_pages": len({item.page for item in locations}) > 1,
            },
        )

    @staticmethod
    def _reading_order_key(
        observation: DeepDocObservation,
    ) -> tuple[float, float, float, int, int]:
        if not observation.locations:
            return (math.inf, math.inf, math.inf, 1, observation.order)
        location = observation.locations[0]
        bbox = location.bbox
        y0 = bbox.y0 if bbox is not None else math.inf
        x0 = bbox.x0 if bbox is not None else math.inf
        kind_rank = 0 if observation.kind == DeepDocObservationKind.TEXT else 1
        return (float(location.page), y0, x0, kind_rank, observation.order)

    @staticmethod
    def _box_style(box: Mapping[str, object]) -> DeepDocStyle | None:
        font_name = DeepDocPdfObservationBuilder._trimmed_string(box.get("font_name") or box.get("font"))
        font_size = DeepDocPdfObservationBuilder._finite_float(box.get("font_size") or box.get("size"))
        if font_size is not None and font_size <= 0:
            font_size = None
        bold = box.get("bold") if isinstance(box.get("bold"), bool) else None
        italic = box.get("italic") if isinstance(box.get("italic"), bool) else None
        if all(item is None for item in (font_name, font_size, bold, italic)):
            return None
        return DeepDocStyle(
            font_name=font_name,
            font_size=font_size,
            bold=bold,
            italic=italic,
        )

    @staticmethod
    def _box_attributes(box: Mapping[str, object]) -> dict[str, DeepDocJsonValue]:
        output: dict[str, DeepDocJsonValue] = {}
        for key in ("layoutno", "col_id", "bullet_type"):
            value = box.get(key)
            if value is None or isinstance(value, (str, bool, int)) or isinstance(value, float) and math.isfinite(value):
                output[key] = value
            else:
                output[key] = str(value)
        return output

    @staticmethod
    def _trimmed_string(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _finite_float(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @staticmethod
    def _diagnostic(
        code: str,
        message: str,
        *,
        affects_structural_completeness: bool = False,
    ) -> DeepDocDiagnostic:
        return DeepDocDiagnostic(
            code=code,
            message=message,
            level=DeepDocDiagnosticLevel.WARNING,
            affects_structural_completeness=affects_structural_completeness,
        )


def _thaw_json_object(
    value: Mapping[str, DeepDocJsonValue],
) -> dict[str, object]:
    return {key: _thaw_json_value(item) for key, item in value.items()}


def _source_title(source_name: str | None) -> str | None:
    if source_name is None:
        return None
    leaf = source_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not leaf:
        return None
    stem, separator, _suffix = leaf.rpartition(".")
    return stem if separator and stem else leaf


def _thaw_json_value(value: DeepDocJsonValue) -> object:
    if isinstance(value, Mapping):
        return _thaw_json_object(value)
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


__all__ = [
    "DEEPDOC_PDF_MEDIA_TYPE",
    "DEEPDOC_PDF_OBSERVATION_BUILDER_VERSION",
    "DEEPDOC_SOURCE_ADAPTER_VERSION",
    "DeepDocPdfObservationBuilder",
    "DeepDocSourceAdapter",
]
