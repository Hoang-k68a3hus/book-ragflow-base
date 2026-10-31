"""Reproducible PDF OCR/layout quality and latency audit.

Run: python -m test.benchmark.pdf_quality_audit --manifest path/to/corpus.jsonl
     --engine deepdoc --runs 7 --warmups 1 --output report.json
Run the same manifest on baseline and proposed Git revisions with the same
hardware, dependencies and engine; pass --baseline baseline.json to compare.
No synthetic/mock OCR result is counted as quality evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def levenshtein(left, right) -> int:
    """Exact Levenshtein distance for strings or token sequences."""
    try:
        import editdistance

        return int(editdistance.eval(left, right))
    except ImportError:
        pass
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def text_errors(reference: str, candidate: str) -> dict[str, int]:
    reference, candidate = normalized_text(reference), normalized_text(candidate)
    return {
        "char_errors": levenshtein(reference, candidate),
        "reference_chars": len(reference),
        "word_errors": levenshtein(reference.split(), candidate.split()),
        "reference_words": len(reference.split()),
    }


def percentile(values: list[float], percentile_value: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    # Nearest-rank percentile; deterministic even with very small samples.
    return round(ordered[max(0, math.ceil(percentile_value / 100 * len(ordered)) - 1)], 3)


def current_rss_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except ImportError:
        try:
            with open("/proc/self/statm", encoding="ascii") as stream:
                resident_pages = int(stream.read().split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError, AttributeError):
            return None


class RSSSampler:
    """Sample whole-process resident memory, including warmed model weights."""

    def __init__(self) -> None:
        self.peak: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            value = current_rss_bytes()
            if value is not None:
                self.peak = value if self.peak is None else max(self.peak, value)
            self._stop.wait(0.02)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_args):
        self._stop.set()
        self._thread.join()
        value = current_rss_bytes()
        if value is not None:
            self.peak = value if self.peak is None else max(self.peak, value)


def observed_text_by_page(result) -> dict[str, str]:
    pages: dict[str, list[str]] = {}
    for item in result.observations:
        if str(item.kind).upper() != "TEXT" or not item.text or not item.locations:
            continue
        # One observation can cover multiple pages; the text must not be
        # duplicated over each page without word-level provenance.
        if len({location.page for location in item.locations}) != 1:
            continue
        page = str(item.locations[0].page)
        pages.setdefault(page, []).append(item.text)
    return {page: "\n".join(blocks) for page, blocks in pages.items()}


def observed_layout(result) -> list[dict]:
    layout = []
    for item in result.observations:
        if not item.locations:
            continue
        kind = str(item.kind).upper()
        if kind == "TEXT" and item.type_hint == "HEADING":
            kind = "HEADING"
        for location in item.locations:
            if location.bbox is None:
                continue
            box = location.bbox
            layout.append(
                {
                    "page": location.page,
                    "kind": kind,
                    "bbox": [
                        box.x0 / location.page_width,
                        box.y0 / location.page_height,
                        box.x1 / location.page_width,
                        box.y1 / location.page_height,
                    ],
                }
            )
    return layout


def iou(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def layout_counts(truth: list[dict], actual: list[dict], threshold: float = 0.5) -> dict[str, int]:
    """One-to-one same-class, same-page IoU matching against annotated boxes.

    Only classes explicitly annotated in truth are evaluated. Unannotated
    background text and layout classes are ignored, not treated as false positives.
    """
    classes = {item["kind"] for item in truth}
    actual = [item for item in actual if item["kind"] in classes]
    candidates = []
    for ti, target in enumerate(truth):
        for ai, candidate in enumerate(actual):
            if target["page"] == candidate["page"] and target["kind"] == candidate["kind"]:
                overlap = iou(target["bbox"], candidate["bbox"])
                if overlap >= threshold:
                    candidates.append((overlap, ti, ai))
    matched_truth, matched_actual = set(), set()
    for _overlap, ti, ai in sorted(candidates, reverse=True):
        if ti not in matched_truth and ai not in matched_actual:
            matched_truth.add(ti)
            matched_actual.add(ai)
    return {"tp": len(matched_truth), "fp": len(actual) - len(matched_actual), "fn": len(truth) - len(matched_truth)}


def evaluate(result, item: dict) -> dict:
    candidates = observed_text_by_page(result)
    counts = {"char_errors": 0, "reference_chars": 0, "word_errors": 0, "reference_words": 0}
    for page, reference in item["truth"].items():
        page_score = text_errors(reference, candidates.get(str(page), ""))
        for key, value in page_score.items():
            counts[key] += value
    # A page without annotated reference text is not automatically a quality pass.
    scores = {
        **counts,
        "cer": round(counts["char_errors"] / max(1, counts["reference_chars"]), 6),
        "wer": round(counts["word_errors"] / max(1, counts["reference_words"]), 6),
        "annotated_pages": len(item["truth"]),
    }
    if "layout" in item:
        layout = layout_counts(item["layout"], observed_layout(result))
        denom = 2 * layout["tp"] + layout["fp"] + layout["fn"]
        scores["layout"] = {
            **layout,
            "f1_at_iou_0_5": round(2 * layout["tp"] / denom, 6) if denom else 1.0,
        }
    return scores


def environment_info() -> dict:
    def run_command(cmd):
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, timeout=5, text=True)
            return (result.stdout or result.stderr).strip().splitlines()[0][:160]
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError):
            return None

    return {
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "poppler": run_command(["pdftotext", "-v"]),
        "git_sha": run_command(["git", "rev-parse", "HEAD"]),
        "omp_threads": os.getenv("OMP_NUM_THREADS"),
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
    }


def load_manifest(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    items = []
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = json.loads(line)
        if not isinstance(item.get("truth"), dict) or not item["truth"]:
            raise ValueError(f"manifest line {line_number}: truth must contain page-number-to-text")
        if not isinstance(item.get("pdf"), str):
            raise ValueError(f"manifest line {line_number}: PDF path missing")
        file_path = (path.parent / item["pdf"]).resolve()
        if not file_path.is_file() or file_path.suffix.lower() != ".pdf":
            raise ValueError(f"manifest line {line_number}: PDF file not found: {file_path}")
        if "layout" in item:
            for element in item["layout"]:
                if (
                    type(element.get("page")) is not int
                    or not isinstance(element.get("kind"), str)
                    or len(element.get("bbox", [])) != 4
                    or not all(isinstance(value, (float, int)) and 0 <= value <= 1 for value in element["bbox"])
                ):
                    raise ValueError(f"manifest line {line_number}: layout uses normalized [0,1] boxes")
        item["_path"] = str(file_path)
        items.append(item)
    if not items:
        raise ValueError("manifest must have at least one real PDF and its transcribed text")
    return items, hashlib.sha256(raw).hexdigest()


def run_trial(file_name: str, binary: bytes, engine: str) -> tuple[object, dict]:
    from rag.app.book import extract_source

    events = []
    start = time.perf_counter()

    def callback(progress=None, msg=""):
        events.append({"offset_ms": round((time.perf_counter() - start) * 1000, 3), "progress": progress, "msg": str(msg)})

    with RSSSampler() as sampler:
        parsed = extract_source(
            file_name,
            binary=binary,
            parser_config={"layout_recognize": "Plain Text" if engine == "plain" else "DeepDOC"},
            callback=callback,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000
    return parsed, {
        "elapsed_ms": round(elapsed_ms, 3),
        "peak_rss_bytes": sampler.peak,
        "observations": len(parsed.observations),
        "callback_events": events,
    }


def summarize(results: list[dict]) -> dict:
    elapsed = [trial["elapsed_ms"] for result in results if result["status"] == "ok" for trial in result["trials"]]
    rss = [trial["peak_rss_bytes"] for result in results if result["status"] == "ok" for trial in result["trials"] if trial["peak_rss_bytes"] is not None]
    text_totals = {
        key: sum(result["quality"][key] for result in results if result["status"] == "ok")
        for key in ("char_errors", "reference_chars", "word_errors", "reference_words")
    }
    layout_totals = {
        key: sum(result["quality"].get("layout", {}).get(key, 0) for result in results if result["status"] == "ok")
        for key in ("tp", "fp", "fn")
    }
    return {
        "completed_documents": sum(result["status"] == "ok" for result in results),
        "failed_documents": [result["pdf"] for result in results if result["status"] != "ok"],
        "timed_trials": len(elapsed),
        "elapsed_ms_p50": percentile(elapsed, 50),
        "elapsed_ms_p95": percentile(elapsed, 95),
        "peak_rss_bytes_p50": percentile(rss, 50),
        "peak_rss_bytes_p95": percentile(rss, 95),
        "cer": round(text_totals["char_errors"] / max(1, text_totals["reference_chars"]), 6),
        "wer": round(text_totals["word_errors"] / max(1, text_totals["reference_words"]), 6),
        "layout_f1_at_iou_0_5": (
            round(2 * layout_totals["tp"] / max(1, 2 * layout_totals["tp"] + layout_totals["fp"] + layout_totals["fn"]), 6)
            if any("layout" in result.get("quality", {}) for result in results) else None
        ),
    }


def compare_baseline(current: dict, baseline: dict) -> dict:
    keys = ("python", "platform", "machine", "cpu_count", "poppler", "omp_threads", "cuda_visible_devices")
    mismatches = [key for key in keys if current["environment"].get(key) != baseline.get("environment", {}).get(key)]
    if current["manifest_sha256"] != baseline.get("manifest_sha256"):
        mismatches.append("manifest_sha256")
    if current["engine"] != baseline.get("engine"):
        mismatches.append("engine")
    if current["runs"] != baseline.get("runs") or current["warmups"] != baseline.get("warmups"):
        mismatches.append("trial_configuration")
    if current["summary"]["failed_documents"] or baseline.get("summary", {}).get("failed_documents"):
        mismatches.append("failed_documents")
    if mismatches:
        return {"comparable": False, "mismatches": mismatches}
    comparison = {"comparable": True, "note": "Same declared environment; verify CPU/GPU load and caches externally."}
    for key in ("elapsed_ms_p50", "elapsed_ms_p95", "peak_rss_bytes_p50", "peak_rss_bytes_p95"):
        old, new = baseline["summary"].get(key), current["summary"].get(key)
        comparison[key + "_change_pct"] = round((new - old) / old * 100, 2) if old and new is not None else None
    return comparison


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--engine", choices=("plain", "deepdoc"), required=True)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="JSON report made on a baseline Git revision")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.warmups < 0:
        parser.error("runs must be >= 1 and warmups >= 0")

    corpus, digest = load_manifest(args.manifest)
    result = {
        "schema": "pdf-quality-audit-v1",
        "manifest_sha256": digest,
        "engine": args.engine,
        "runs": args.runs,
        "warmups": args.warmups,
        "environment": environment_info(),
        "results": [],
    }
    for item in corpus:
        path = Path(item["_path"])
        binary = path.read_bytes()
        record = {
            "pdf": item["pdf"],
            "pdf_sha256": hashlib.sha256(binary).hexdigest(),
            "tags": item.get("tags", []),
            "status": "ok",
            "trials": [],
        }
        try:
            for _ in range(args.warmups):
                run_trial(str(path), binary, args.engine)
            parsed = None
            for _ in range(args.runs):
                parsed, sample = run_trial(str(path), binary, args.engine)
                record["trials"].append(sample)
            record["quality"] = evaluate(parsed, item)
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
        result["results"].append(record)
    result["summary"] = summarize(result["results"])
    if args.baseline:
        result["baseline_comparison"] = compare_baseline(result, json.loads(args.baseline.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "baseline_comparison": result.get("baseline_comparison")}, ensure_ascii=False, indent=2))
    return 1 if result["summary"]["failed_documents"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
