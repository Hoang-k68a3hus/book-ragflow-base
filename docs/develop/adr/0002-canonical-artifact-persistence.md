# ADR 0002: Deterministic canonical artifacts and registry

- Status: Accepted
- Date: 2026-08-24
- Scope: PR3 canonical serialization, immutable blob storage, and DB registry

## Context

`CanonicalDocument` is the durable source-understanding result. Search documents
must refer to it without embedding a second mutable copy of its structure. A
canonical artifact therefore needs stable bytes, integrity verification, and an
authoritative registry entry that can be resolved independently of an index.

## Decision

Canonical documents use serialization contract version `1`:

- UTF-8 JSON without a BOM or trailing newline;
- recursively sorted object keys;
- no insignificant whitespace;
- non-ASCII source text is preserved rather than escaped;
- NaN and Infinity are rejected;
- arrays preserve schema order;
- all declared fields, including `null` and defaults, are retained.

Deserialization is strict by default: JSON that validates as a
`CanonicalDocument` but is not in canonical encoding is rejected.

Canonical JSON is hashed before storage encoding. Object storage uses
deterministic gzip (`mtime=0`) by default. The registry records both integrity
domains: `artifact_hash`/`byte_size` describe canonical JSON, while
`stored_hash`/`stored_byte_size` and `content_encoding` describe the exact blob
bytes. Loading verifies stored bytes before decoding and canonical bytes after
decoding.

The object key contains the canonical schema version, exact source SHA-256, and
canonical-artifact SHA-256. The source hash and schema version establish the
business identity; the artifact hash makes each stored blob content-addressed and
prevents a concurrent producer with different bytes from overwriting it.

The `canonical_document_artifact` DB table stores references and integrity facts,
not canonical JSON. Its primary key is deterministically derived from tenant,
RAGFlow document, source revision, source content hash, and canonical schema
version. Registration is create-only and idempotent. Reusing that identity with
different immutable fields is a conflict.

## Ownership

- `rag.source_understanding.artifacts` owns serialization, artifact identity,
  integrity checks, and the storage protocol.
- `api.db.services.canonical_artifact_service` adapts RAGFlow object storage and
  owns registry transactions.
- `api.db.db_models.CanonicalDocumentArtifact` owns only the registry schema.
- Search/index integration remains outside PR3 and will store only the artifact
  reference plus retrieval/citation projection fields.

## Failure and concurrency policy

Blob creation happens before registry insertion. A DB failure can leave an
unreferenced content-addressed blob, which is safe and can be garbage-collected
later. A registry entry is never created before its blob has been written and
read back successfully.

The current RAGFlow storage interface has no portable conditional-put operation.
The content-addressed artifact hash prevents different payloads from sharing an
object key; the DB registry resolves races over the logical source revision and
rejects conflicting results.

## Database rollout

The registry is an additive Peewee model. Normal RAGFlow startup discovers it in
`init_database_tables` and creates the missing table. Deployments that disable
automatic initialization must generate and apply the corresponding additive
migration with `tools/scripts/db_schema_sync.py` before enabling persistence.

## Rejected alternatives

- Storing canonical JSON in the DB: this duplicates large immutable content and
  makes the registry the blob store.
- Keying only by RAGFlow document ID: retries and revisions could overwrite prior
  artifacts.
- Omitting the artifact hash from the object key: storage backends without atomic
  conditional writes could overwrite conflicting bytes.
- Permissive JSON loading: semantically equivalent but byte-different artifacts
  undermine hashing, caching, and reproducibility.
