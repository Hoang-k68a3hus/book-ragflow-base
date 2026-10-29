# ADR 0003: Retrieval-unit index projection

- Status: Accepted
- Date: 2026-08-24
- Scope: PR4 retrieval projection and search-backend mappings

## Context

The canonical artifact is the durable document representation. Search indexes are
rebuildable accelerators and must not become a second mutable canonical store.
RAGFlow nevertheless requires routing fields, searchable text, tokenizer output,
and citation facts on each search row.

## Decision

`RetrievalUnitProjector` converts validated `RetrievalUnit` values into a single
backend-neutral `RetrievalUnitIndexDocument` contract. Projection order follows
the first canonical element order and uses the retrieval-unit ID as a stable tie
breaker.

Each index row contains only:

- RAGFlow routing and search fields: `id`, `doc_id`, `kb_id`, `docnm_kwd`,
  deterministic `chunk_order_int`, retrieval text,
  tokenizer text, token count, availability, and unit type;
- `retrieval_unit_id`, `element_ids`, optional logical/subdocument identity, and
  exact source content/revision identity;
- the compact `context_path` required to explain retrieval context;
- citation-safe `source_anchors` required to project results back to source;
- `canonical_artifact_ref`, containing the DB registry ID and integrity/schema
  versions, but not the storage bucket or object key.
- detected language and document-level text/order/structure/overall quality;
- retrieval-unit lexical anomaly count and boolean anomaly marker, derived from
  the typed canonical lexical-quality section and the row's `element_ids`.
- a bounded `quality_multiplier` (default floor `0.75`) used only to reorder
  otherwise eligible candidates. Similarity thresholds continue to evaluate the
  unmodified retrieval score, so extraction defects never hard-filter source
  content.

Display text, full document metadata, relations, semantic graph payloads, assets, and
the canonical artifact body are not copied into the index.

The projector accepts tokenizer functions and an explicit tokenizer version. It
does not import RAGFlow tokenizer/runtime modules, so source understanding remains
independent of ingestion services.

Projection version `4` verifies the canonical byte size and artifact hash against
the exact document being projected. Matching the PDF hash and schema alone is
insufficient: two processing runs can produce different structure for one PDF.

`Dealer.search` selects canonical identity, context, anchors, language and quality
fields; `Dealer.retrieval` carries them through reranking into each result chunk.
Backend field conversion preserves JSON objects/lists, booleans and numeric
quality values. Infinity decodes its serialized JSON columns on read.

## Backend mappings

- Elasticsearch and OpenSearch use explicit keyword/scalar fields, nested
  `context_path` and `source_anchors`, and a non-dynamic artifact-reference
  object.
- Infinity stores list/object projection fields in JSON columns and serializes
  them deterministically on insert/update.
- OceanBase uses ARRAY columns for canonical ID lists and JSON columns for
  context, anchors, and artifact reference. These fields are additive migration
  columns for existing chunk tables.

Existing Elasticsearch/OpenSearch indexes require recreation or reindexing to
receive the explicit nested mappings. Infinity and OceanBase use their existing
additive-column migration paths.

## Consequences

- An index can be deleted and rebuilt from canonical artifacts and deterministic
  retrieval-unit generation.
- Search results can resolve the authoritative artifact through the registry and
  produce citations without storing the full canonical document.
- Book PDF ingestion uses this projection through the service described in
  ADR 0004. Returning provenance does not automatically load entire chapters
  into chat context; canonical loading remains an explicit registry operation.

## Rejected alternatives

- Indexing `CanonicalDocument` directly: duplicates durable structure and makes
  schema evolution dependent on every search backend.
- Storing the object-storage key in every row: leaks storage topology and bypasses
  registry ownership.
- Letting each backend invent a projection: field drift would make rebuilds and
  cross-backend tests nondeterministic.
