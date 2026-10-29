from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

# Match server bootstrap order for settings/Redis/query imports.
from common import settings  # noqa: F401
from rag.nlp.search import Dealer
from rag.source_understanding.retrieval_units.fields import CANONICAL_RETRIEVAL_FIELDS


def _row():
    return {
        "retrieval_unit_id": "ru-1",
        "doc_id": "doc-1",
        "kb_id": "kb-1",
        "docnm_kwd": "book.pdf",
        "content_ltks": "book text",
        "content_with_weight": "Book text",
        "element_ids": ["el-1"],
        "logical_unit_ids": [],
        "context_path": [],
        "source_anchors": [{"element_id": "el-1", "page": 2}],
        "canonical_artifact_ref": {"registry_id": "a" * 64, "content_encoding": "gzip"},
        "language": "mixed",
        "document_order_quality": 0.825,
        "quality_multiplier": 0.75,
        "has_text_anomaly": True,
        "text_anomaly_count": 2,
    }


@pytest.mark.parametrize("backend", ["elasticsearch", "opensearch", "infinity"])
def test_backend_preserves_canonical_field_types(backend):
    from rag.utils.es_conn import ESConnection
    from rag.utils.opensearch_conn import OSConnection
    from rag.utils.infinity_conn import InfinityConnection

    factory = {"elasticsearch": ESConnection, "opensearch": OSConnection, "infinity": InfinityConnection}[backend]
    # Exercise conversion only, without opening a network connection in __init__.
    cls = inspect.getclosurevars(factory).nonlocals["cls"]
    connection = object.__new__(cls)
    row = _row()
    if backend == "infinity":
        data = pd.DataFrame([{**row, "id": "ru-1", "canonical_artifact_ref": json.dumps(row["canonical_artifact_ref"])}])
    else:
        data = {"hits": {"hits": [{"_id": "ru-1", "_source": row.copy(), "_score": 1.0}]}}
    fields = connection.get_fields(data, list(CANONICAL_RETRIEVAL_FIELDS))["ru-1"]
    for name in ("canonical_artifact_ref", "source_anchors", "element_ids", "document_order_quality", "text_anomaly_count", "has_text_anomaly"):
        assert fields[name] == row[name]
        assert type(fields[name]) is type(row[name])


@pytest.mark.asyncio
async def test_retrieval_returns_provenance_and_does_not_filter_by_penalized_score():
    dealer = Dealer(Mock())
    row = _row()
    result = Dealer.SearchResult(total=1, ids=["ru-1"], query_vector=[], field={"ru-1": row})
    dealer.search = AsyncMock(return_value=result)
    dealer._existing_doc_ids = AsyncMock(return_value={"doc-1"})
    dealer.rerank_by_model = Mock(return_value=([0.8], [0.8], [0.8]))
    found = await dealer.retrieval(
        "book text",
        None,
        ["tenant-1"],
        ["kb-1"],
        1,
        10,
        similarity_threshold=0.7,
        rerank_mdl=Mock(),
    )
    assert found["total"] == 1  # raw 0.8 passes even though penalized 0.6 < 0.7
    chunk = found["chunks"][0]
    assert chunk["similarity"] == pytest.approx(0.6)
    for name in CANONICAL_RETRIEVAL_FIELDS:
        if name in row:
            assert chunk[name] == row[name]
