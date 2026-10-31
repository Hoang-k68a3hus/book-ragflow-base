"""Unit tests for metric math; these are NOT real OCR accuracy measurements."""

import json
from types import SimpleNamespace

import pytest

from test.benchmark.pdf_quality_audit import (
    compare_baseline,
    evaluate,
    iou,
    layout_counts,
    load_manifest,
    percentile,
    text_errors,
)


def test_unicode_normalization_prevents_false_vietnamese_accent_errors():
    score = text_errors("Tiếng Việt", "Tie\u0302\u0301ng Viê\u0323t")
    assert score["char_errors"] == 0
    assert score["word_errors"] == 0


def test_character_and_word_errors_are_independent():
    score = text_errors("a b c", "a x c")
    assert score == {
        "char_errors": 1,
        "reference_chars": 5,
        "word_errors": 1,
        "reference_words": 3,
    }


def test_layout_matching_is_one_to_one_and_uses_normalized_boxes():
    expected = [{"page": 1, "kind": "TABLE", "bbox": [0.1, 0.1, 0.4, 0.4]}]
    actual = expected + expected
    assert iou(expected[0]["bbox"], expected[0]["bbox"]) == pytest.approx(1)
    assert layout_counts(expected, actual) == {"tp": 1, "fp": 1, "fn": 0}


def test_evaluator_uses_real_observation_contract_not_mock_ocr_metrics():
    loc = SimpleNamespace(
        page=1,
        page_width=100,
        page_height=100,
        bbox=SimpleNamespace(x0=10, y0=10, x1=40, y1=40),
    )
    observation = SimpleNamespace(
        kind="TEXT", text="Xin chào", type_hint="HEADING", locations=(loc,)
    )
    result = SimpleNamespace(observations=(observation,))
    score = evaluate(
        result,
        {
            "truth": {"1": "Xin chào"},
            "layout": [{"page": 1, "kind": "HEADING", "bbox": [0.1, 0.1, 0.4, 0.4]}],
        },
    )
    assert score["cer"] == score["wer"] == 0
    assert score["layout"]["f1_at_iou_0_5"] == 1


def test_manifest_requires_real_existing_pdf_and_human_transcription(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"pdf": "absent.pdf", "truth": {"1": "text"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        load_manifest(manifest)
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-placeholder")
    manifest.write_text(json.dumps({"pdf": "doc.pdf", "truth": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="truth"):
        load_manifest(manifest)


def test_nearest_rank_percentiles_and_baseline_gate():
    assert percentile([1, 2, 3, 4, 5], 95) == 5
    current = {
        "manifest_sha256": "a",
        "engine": "plain",
        "runs": 5,
        "warmups": 1,
        "environment": {"python": "3.13"},
        "summary": {"failed_documents": [], "elapsed_ms_p50": 100, "elapsed_ms_p95": 150},
    }
    baseline = {
        **current,
        "summary": {"failed_documents": [], "elapsed_ms_p50": 200, "elapsed_ms_p95": 300},
    }
    compare = compare_baseline(current, baseline)
    assert compare["comparable"]
    assert compare["elapsed_ms_p50_change_pct"] == -50
    mismatch = compare_baseline({**current, "manifest_sha256": "b"}, baseline)
    assert not mismatch["comparable"]
    assert "manifest_sha256" in mismatch["mismatches"]
