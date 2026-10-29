# ADR 0004: Feature-gated canonical ingestion for books

- Status: Accepted
- Date: 2026-08-24
- Scope: PR5 production ingestion switch

## Context

PR2 introduced typed DeepDOC PDF observations, PR3 introduced immutable canonical
artifacts and their database registry, and PR4 introduced retrieval-unit index
projection. None of those changes switched the production chunking path.

Canonical structure is a whole-source property. Splitting a PDF into independent
page tasks would create competing partial canonical documents with the same source
identity and would prevent reliable chapter hierarchy, cross-page tables, reading
order, and citations.

## Decision

The first production slice is opt-in through the document parser configuration:

```json
{
  "source_understanding": {
    "enabled": true
  }
}
```

Absence means disabled. The value must be a JSON object and `enabled` must be a
JSON boolean. The flag is intentionally parser-scoped instead of a process-wide
environment variable so rollout and rollback can happen document by document.

PR5 activates the new path only when all of these conditions hold:

- parser ID is `book`;
- the source filename ends in `.pdf` case-insensitively;
- `source_understanding.enabled` is `true`.

Other formats and parsers continue through their existing parser. The enabled PDF
path requires `layout_recognize` to resolve to `DeepDOC`; it does not silently
substitute DeepDOC for another selected extractor.

When the flag is enabled for a book PDF, task creation emits exactly one page task
covering page zero through the detected PDF page count. Configured `pages` and
`task_page_size` are deliberately ignored because a selected-page artifact must
not claim to be the canonical representation of the exact whole-file hash. If the
page count cannot be read while queueing, the task uses RAGFlow's all-pages upper
bound. The executor independently rejects a partial task before extraction.

The ingestion path is:

1. `rag.app.book.extract_source` performs one DeepDOC pass and returns typed
   observations.
2. `SourceAdapterRunner` builds and validates the `CanonicalDocument`.
3. `RetrievalUnitBuilder` derives provenance-safe retrieval units using the
   configured chunk token budget. An empty projection fails ingestion.
4. `CanonicalArtifactPersistenceService` persists and registers the immutable
   artifact after the build succeeds.
5. `RetrievalUnitProjector` produces index rows whose IDs remain the retrieval-unit
   IDs.
6. The existing RAGFlow post-processing, embedding, and index insertion stages
   consume those rows.

The stable document creation timestamp is used as the canonical processing time.
This keeps serialized artifact bytes and registry identity idempotent across task
retries. Older records without that timestamp use the Unix epoch rather than a
non-deterministic current time.

The token budget is validated before extraction or persistence. Whole-element
integrity can require exceeding this target: oversized units and unresolved,
blank or excluded elements are recorded by both executor paths. Unresolved and
oversized counts also appear in task progress. A successful task must not imply
that every preserved element was included in the retrieval index.

## Consequences

- Enabling the flag requires re-parsing the document so task boundaries and index
  rows are rebuilt.
- Rollback is immediate: set `enabled` to `false` and re-parse to return to legacy
  book chunks. Immutable artifacts already written are retained.
- Large books trade page-task parallelism for a coherent canonical graph. Worker
  memory, duration, and retrieval quality must be measured during staged rollout.
- PDF is the only PR5 canonical book format. DOC/DOCX/TXT/HTML remain on the legacy
  book path until their typed adapters are integrated in later changes.
- The original and refactored executors use the same ingestion service, preventing
  feature behavior from depending on `TE_RUN_MODE`.

## Rejected alternatives

- A global environment flag: too coarse for staged rollout and difficult to bind
  to the task digest.
- Building one canonical artifact per page task: breaks whole-document structure
  and creates ambiguous source ownership.
- Running legacy chunking and canonical ingestion together: duplicates DeepDOC
  work and creates two competing index representations.
- Falling back silently after a canonical-path failure: would mix index contracts
  under one enabled configuration and hide correctness failures.
