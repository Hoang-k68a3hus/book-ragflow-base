"""Source-understanding fields carried from index rows to retrieval results."""

CANONICAL_RETRIEVAL_FIELDS = (
    "retrieval_unit_id",
    "content_hash",
    "source_revision",
    "subdocument_id",
    "element_ids",
    "logical_unit_ids",
    "context_path",
    "source_anchors",
    "canonical_artifact_ref",
    "unit_type",
    "language",
    "document_text_quality",
    "document_order_quality",
    "document_structure_quality",
    "document_overall_quality",
    "text_anomaly_count",
    "has_text_anomaly",
    "quality_multiplier",
)

CANONICAL_JSON_FIELDS = frozenset(
    {
        "element_ids",
        "logical_unit_ids",
        "context_path",
        "source_anchors",
        "canonical_artifact_ref",
    }
)
