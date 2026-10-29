"""Opt-in HTTP upload -> Redis worker -> canonical -> embeddings -> retrieval.

Requires a running API/worker and a real TEI endpoint. Keeps its isolated dataset
and a report for inspection; never changes an existing user's model settings.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from pypdf import PdfReader

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SOURCE_UNDERSTANDING_HTTP_E2E") != "1",
    reason="requires API, worker, TEI and SOURCE_UNDERSTANDING_PDF_PATH",
)


def test_real_book_http_ingestion_and_retrieval():
    from common import settings
    from api.db.db_models import CanonicalDocumentArtifact, Document, Knowledgebase, Task, User
    from api.db.services.canonical_artifact_service import CanonicalArtifactPersistenceService
    from api.utils.crypt import crypt

    resume_path = os.getenv("SOURCE_UNDERSTANDING_E2E_RESUME_REPORT")
    previous = json.loads(Path(resume_path).read_text(encoding="utf-8")) if resume_path else None
    if previous and previous.get("status") == "failed":
        previous.setdefault("prior_attempts", []).append(
            {
                "status": previous["status"],
                "error": previous.get("error"),
                "seconds": previous.get("seconds"),
                "timeout_seconds": previous.get("timeout_seconds", 3600),
            }
        )
        previous.pop("error", None)
        previous["status"] = "started"
    source = Path(previous["source"] if previous else os.environ["SOURCE_UNDERSTANDING_PDF_PATH"]).resolve(strict=True)
    assert source.suffix.lower() == ".pdf"
    page_count = len(PdfReader(source).pages)
    api = os.getenv("RAGFLOW_TEST_API", "http://127.0.0.1:9380").rstrip("/")
    tei = os.getenv("RAGFLOW_TEST_TEI", "http://127.0.0.1:6380").rstrip("/")
    model = os.getenv("RAGFLOW_TEST_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    layout_recognize = previous.get("layout_recognize", "DeepDOC") if previous else os.getenv("RAGFLOW_TEST_LAYOUT_RECOGNIZE", "DeepDOC")
    questions = json.loads(os.getenv("SOURCE_UNDERSTANDING_E2E_QUESTIONS", '["cảng biển và vận tải container", "container ports and shipping industry"]'))
    assert isinstance(questions, list) and questions and all(isinstance(question, str) and question.strip() for question in questions)
    run_id = previous["run_id"] if previous else uuid4().hex
    output = Path(resume_path).parent if previous else Path(os.getenv("SOURCE_UNDERSTANDING_E2E_OUTPUT", ".runtime/source-understanding-e2e")) / run_id
    output.mkdir(parents=True, exist_ok=bool(previous))
    report = previous or {"run_id": run_id, "source": str(source), "page_count": page_count, "embedding_model": model, "status": "started"}
    timeout_seconds = int(os.getenv("SOURCE_UNDERSTANDING_E2E_TIMEOUT", "3600"))
    report["timeout_seconds"] = timeout_seconds
    report["questions"] = questions
    report["layout_recognize"] = layout_recognize
    session = requests.Session()

    def save_report():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def call(method, route, **kwargs):
        response = session.request(method, api + route, timeout=120, **kwargs)
        response.raise_for_status()
        payload = response.json()
        assert payload.get("code") == 0, f"{method} {route}: {payload.get('message')}"
        print(f"{method} {route}: OK", flush=True)
        return response, payload.get("data")

    save_report()
    started = time.monotonic()
    try:
        for attempt in range(120):
            try:
                health = requests.get(tei + "/health", timeout=5)
                if health.status_code == 200:
                    break
            except requests.RequestException:
                pass
            if attempt % 6 == 0:
                print("Waiting for real TEI model to become ready...", flush=True)
            time.sleep(5)
        else:
            pytest.fail("TEI model did not become ready")
        if previous:
            settings.init_settings()
            account = User.get_by_id(report["tenant_id"])
            assert account.email == f"su-{run_id}@example.com", "resume only this test's isolated account"
            kb_id, doc_id = report["kb_id"], report["doc_id"]
            assert Knowledgebase.get_by_id(kb_id).tenant_id == account.id
            assert Document.get_by_id(doc_id).kb_id == kb_id
            session.headers["Authorization"] = account.get_id()
            user = {"id": account.id}
            report["resumed_verification"] = True
            print(f"Resume verification for existing document {doc_id}; no extraction requeued", flush=True)
        else:
            response, user = call(
                "POST",
                "/api/v1/users",
                json={
                    "nickname": "Source Understanding Test",
                    "email": f"su-{run_id}@example.com",
                    "password": crypt(secrets.token_urlsafe(24)),
                },
            )
            auth = response.headers.get("Authorization")
            assert auth, "registration did not return an authorization header"
            session.headers["Authorization"] = auth
            report["tenant_id"] = user["id"]
            save_report()
            call("PUT", "/api/v1/providers", json={"provider_name": "HuggingFace"})
            call(
                "POST",
                "/api/v1/providers/HuggingFace/instances",
                json={
                    "instance_name": "source-understanding-local",
                    "api_key": "",
                    "base_url": tei,
                    "model_info": [{"model_name": model, "model_type": ["embedding"], "max_tokens": 512}],
                },
            )
            call(
                "PATCH",
                "/api/v1/models/default",
                json={
                    "model_provider": "HuggingFace",
                    "model_instance": "source-understanding-local",
                    "model_name": model,
                    "model_type": "embedding",
                },
            )
            _, dataset = call(
                "POST",
                "/api/v1/datasets",
                json={
                    "name": f"Source Understanding E2E {run_id[:8]}",
                    "chunk_method": "book",
                    "parser_config": {"layout_recognize": layout_recognize, "chunk_token_num": 256, "auto_keywords": 0, "auto_questions": 0, "source_understanding": {"enabled": True}},
                },
            )
            kb_id = dataset["id"]
            report["kb_id"] = kb_id
            save_report()
            with source.open("rb") as stream:
                _, uploaded = call("POST", f"/api/v1/datasets/{kb_id}/documents", files={"file": (source.name, stream, "application/pdf")})
            doc_id = uploaded[0]["id"]
            report["doc_id"] = doc_id
            save_report()
            _, queued = call("POST", f"/api/v1/datasets/{kb_id}/documents/parse", json={"document_ids": [doc_id]})
            assert queued["success_count"] == 1
        settings.init_settings()
        deadline = time.monotonic() + timeout_seconds
        if previous:
            task_created = min(task.create_time for task in Task.select().where(Task.doc_id == doc_id)) / 1000
            deadline = time.monotonic() + max(0, task_created + timeout_seconds - time.time())
        last_message = None
        while time.monotonic() < deadline:
            document = Document.get_by_id(doc_id)
            progress = float(document.progress or 0)
            message = document.progress_msg or ""
            report.update({"progress": progress, "progress_msg": message, "run": str(document.run)})
            save_report()
            if message != last_message:
                print(f"progress={progress:.3f} {message[-800:]}", flush=True)
                last_message = message
            assert progress >= 0, f"worker failed: {message}"
            if str(document.run) == "3" and progress >= 1:
                break
            time.sleep(5)
        else:
            pytest.fail(f"ingestion timeout: {report['progress_msg']}")
        tasks = list(Task.select().where(Task.doc_id == doc_id))
        assert len(tasks) == 1, "whole-document mode must queue exactly one task"
        assert tasks[0].from_page == 0 and tasks[0].to_page >= page_count
        records = list(CanonicalDocumentArtifact.select().where(CanonicalDocumentArtifact.document_id == doc_id))
        assert len(records) == 1
        record = records[0]
        canonical = CanonicalArtifactPersistenceService.load(record.id, tenant_id=user["id"])
        if layout_recognize == "Plain Text":
            extractor = canonical.metadata.attributes["deepdoc"]
            assert extractor["parser_name"] == "poppler-pdftotext"
            assert extractor["attributes"]["ocr_used"] is False
            assert "OCR started" not in report["progress_msg"]
            assert extractor["attributes"]["page_count"] == page_count
            report["ocr_used"] = False
            report["native_text_page_count"] = extractor["attributes"]["text_page_count"]
        assert canonical.content_hash == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        stored = settings.STORAGE_IMPL.get(record.storage_bucket, record.artifact_key, tenant_id=user["id"])
        assert record.content_encoding == "gzip" and stored
        (output / "canonical-document.json.gz").write_bytes(stored)
        assert gzip.decompress(stored)
        store = settings.docStoreConn
        index = f"ragflow_{user['id']}"
        indexed = store.es.search(index=index, query={"term": {"doc_id": doc_id}}, size=1000)
        rows = indexed["hits"]["hits"]
        assert rows
        assert indexed["hits"]["total"]["value"] == document.chunk_num
        element_ids = {element.id for element in canonical.elements}
        for hit in rows:
            row = hit["_source"]
            assert row["id"] == row["retrieval_unit_id"]
            assert row["canonical_artifact_ref"]["registry_id"] == record.id
            assert set(row["element_ids"]) <= element_ids
            assert row["source_anchors"]
            assert any(key.startswith("q_") and key.endswith("_vec") and value for key, value in row.items()), "real embedding vector missing"
        retrieval = {}
        for question in questions:
            _, found = call(
                "POST",
                "/api/v1/retrieval",
                json={
                    "question": question,
                    "dataset_ids": [kb_id],
                    "document_ids": [doc_id],
                    "page": 1,
                    "page_size": 5,
                    "similarity_threshold": 0.0,
                    "vector_similarity_weight": 0.5,
                },
            )
            assert found["chunks"], f"no retrieval hits for {question!r}"
            for chunk in found["chunks"]:
                assert chunk["retrieval_unit_id"]
                assert chunk["canonical_artifact_ref"]["registry_id"] == record.id
                assert chunk["source_anchors"]
            retrieval[question] = found
        (output / "retrieval.json").write_text(json.dumps(retrieval, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "index-sample.json").write_text(json.dumps([hit["_source"] for hit in rows[:5]], ensure_ascii=False, indent=2), encoding="utf-8")
        report.update(
            {
                "status": "passed",
                "seconds": round(time.monotonic() - started, 2),
                "task_count": len(tasks),
                "seconds_since_task_created": round(time.time() - tasks[0].create_time / 1000, 2),
                "element_count": len(canonical.elements),
                "context_node_count": len(canonical.context_nodes),
                "chunk_count": document.chunk_num,
                "registry_id": record.id,
                "content_encoding": record.content_encoding,
                "canonical_byte_size": record.byte_size,
                "stored_byte_size": record.stored_byte_size,
                "quality": canonical.quality.model_dump(mode="json"),
            }
        )
        print(f"E2E report: {output / 'report.json'}", flush=True)
    except BaseException as error:
        report.update({"status": "failed", "error": str(error)[:4000], "seconds": round(time.monotonic() - started, 2)})
        raise
    finally:
        save_report()
        session.close()
