# ADR 0001: Source-understanding ownership and DeepDOC boundary

- Status: Accepted
- Date: 2026-08-24
- Scope: P0 package normalization and DeepDOC PDF observation bridge

## Context

RAGFlow's existing parser modules combine extraction, structural heuristics, token
chunking, and search-document projection. The new `rag.source_understanding`
package needs source-near observations before token chunking so it can preserve
source identity, infer structure once, and build deterministic retrieval units.

Passing existing `content_with_weight` chunk dictionaries into source
understanding is too late: grouping and token-size decisions have already removed
or merged source observations.

## Decision

Ownership is split as follows:

1. `deepdoc` owns OCR, native-text extraction, layout observations, page geometry,
   bounding boxes, and table observations.
2. `rag.source_understanding` owns normalization, preservation checks, logical
   grouping, hierarchy, structural relations, canonical assembly, and retrieval-
   unit projection.
3. ingestion owns task orchestration, persistence, embedding, indexing, retries,
   and cancellation.

DeepDOC exposes source facts through the dependency-free typed contract in
`deepdoc.parser.source_observation`. DeepDOC does not import RAG packages.

`DeepDocSourceAdapter` lives in `rag.source_understanding.adapters` and converts a
bound `DeepDocParseResult` plus the exact input bytes into `SourceAdapterResult`.
The adapter computes SHA-256 from the exact bytes; it never trusts a caller-
supplied hash.

All imports use the single canonical package namespace
`rag.source_understanding`. No top-level `source_understanding` alias or
`PYTHONPATH` shim is supported.

## Source-fact policy

- Pages are 1-based in the typed contract and canonical schema.
- DeepDOC bounding boxes use page-local absolute coordinates and carry the page
  dimensions required for canonical normalization.
- A bounding box outside its declared page is not clamped. The adapter keeps the
  page and raw geometry in attributes and emits a diagnostic.
- Merged DeepDOC output whose native/OCR origin cannot be proven is marked
  `DERIVED`.
- Missing page, bounding box, style, or type information remains missing.
- Multi-page table locations remain an ordered location list in attributes; the
  first location is the canonical primary location.

## P0 runtime scope

P0 exposes the typed bridge for the `book` PDF parser but does not yet change the
production task executor, artifact storage, embedding, or index mappings. Semantic
enrichment is outside this milestone.

The current `book.Pdf.__call__` output remains the existing chunking input.
`book.Pdf.extract_source` reuses the same DeepDOC extraction work and returns a
typed `DeepDocParseResult` before RAGFlow token chunking.

## Consequences

- Source understanding can be tested without storage or search services.
- The current ingestion path is unchanged until a later integration milestone.
- Format-specific bridges can be added behind the same typed contract without
  adding dependencies from DeepDOC to RAG.
- DeepDOC PDF observations are conservative derived facts until extraction can
  distinguish native and OCR provenance at observation granularity.

## Rejected alternatives

- Moving source-understanding logic into `deepdoc`: this makes extraction own
  hierarchy, semantics, and retrieval concerns.
- Adapting final RAGFlow chunks: source identity and integrity have already been
  altered by chunking.
- Adding a top-level package shim: two import paths can create distinct runtime
  model identities and hide packaging errors.
