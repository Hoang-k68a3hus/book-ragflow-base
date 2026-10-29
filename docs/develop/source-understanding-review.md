# Source Understanding review — 2026-10-18

## Assessment

The ownership boundary is sound: DeepDOC extracts observations; Source
Understanding preserves and interprets document structure; RAGFlow orchestrates
tasks, persistence, embedding and search. The canonical artifact is authoritative
and the index is a rebuildable projection. The current layer performs structural
understanding and quality diagnostics, not verified semantic comprehension.

## Corrections from this review

| Finding | Result |
| --- | --- |
| A different canonical result for the same PDF could be referenced by the projector | Projection checks the exact serialized artifact hash and byte size |
| Index rows lacked document names and stable chunk order | Projection v4 emits `docnm_kwd` and `chunk_order_int` |
| Search discarded canonical provenance | Search selects it and retrieval carries it through reranking |
| Backend conversion could stringify the artifact reference and quality values | ES/OpenSearch preserve field types; Infinity decodes canonical JSON columns |
| Invalid token budgets failed after storage writes | Validation runs before extraction; empty builds fail before persistence |
| Retrieval coverage limitations were not fully exposed | Both executors record oversized, unresolved, blank and excluded IDs; task progress warns about unresolved/oversized content |
| Concurrent ingestion changed the shared tokenizer language | Each ingestion task uses its own tokenizer configuration, version 2 |
| The service integration test replaced the real database model | It now uses the production model, persistence service, configured storage, ES connector, tokenizer and `Dealer.search` |
| The `book` parser bypassed `TABLE_AUTO_ROTATE` | Book extraction now passes the configured rotation policy to DeepDOC |
| One untrusted inferred heading aborted a real book ingestion | Inferred structural elements without a HARD/SOFT incoming boundary are suppressed and recorded in hierarchy diagnostics |

The real integration test exposed four missing inherited timestamp columns in
the local registry table created by the former test model. They were added from
the actual Peewee field definitions without removing existing rows. Fresh
databases obtain those fields from the production model.

## Verification boundary

- Focused unit tests cover preservation, hierarchy guards, Vietnamese/English
  lexical diagnostics, order quality, deterministic storage, projector integrity,
  backend value types, rerank provenance and task ingestion behavior.
- Real MySQL/MinIO/Elasticsearch testing verifies gzip round-trip, idempotent
  registration, tenant isolation, immutable-field conflict, index insertion,
  provenance retrieval and Vietnamese/English keyword queries. Each successful
  run deletes only its own generated rows and objects.
- Integration input is synthetic typed observations. This test does **not**
  establish real PDF extraction accuracy, HTTP upload/queue/worker completion,
  embedding quality or an end-to-end chat result. No DeepSeek key is required.

Run the opt-in service test separately from unit tests that replace imported
modules. In PowerShell, from the project root:

```powershell
. .\tools\scripts\enter_local_runtime.ps1
$env:RUN_SOURCE_UNDERSTANDING_SERVICE_INTEGRATION = '1'
.venv\Scripts\python.exe -m pytest test/integration_test/source_understanding/test_real_services.py -q -o cache_dir=.runtime/pytest-cache
```

This uses the configured development services and puts runtime/cache files on D.

For the opt-in HTTP/worker test, start the API and refactored worker, configure a
real TEI embedding endpoint, then run:

```powershell
$env:RUN_SOURCE_UNDERSTANDING_HTTP_E2E = '1'
$env:SOURCE_UNDERSTANDING_PDF_PATH = 'D:\book\Apr2025FPTSMaritimePortIndustryReport_d44f733d.pdf'
.venv\Scripts\python.exe -m pytest test/integration_test/source_understanding/test_book_http_e2e.py -q -s -o cache_dir=.runtime/pytest-cache
```

The test creates its own account, model configuration and dataset through HTTP,
uploads the complete source, waits for the worker, checks the canonical hash,
registry, index vectors and retrieval provenance, and keeps reports/artifacts
under `.runtime/source-understanding-e2e/<run_id>/`. It intentionally keeps its
test dataset for inspection. No existing tenant's defaults are modified.
`RAGFLOW_TEST_API`, `RAGFLOW_TEST_TEI` and `RAGFLOW_TEST_EMBEDDING_MODEL` select
the local endpoints/model. The default model is `intfloat/multilingual-e5-small`.
The default retrieval questions concern the maritime report above; their results
are smoke checks, not a labeled relevance benchmark.

If the test client is interrupted while the worker continues, set
`SOURCE_UNDERSTANDING_E2E_RESUME_REPORT` to that run's `report.json` and rerun the
same test. Resume validates the isolated account and document ownership, then
continues verification without uploading or requeueing. Its timeout remains
anchored to the original task creation time; the report records the resumed run.
The default ingestion timeout is 3600 seconds. `SOURCE_UNDERSTANDING_E2E_TIMEOUT`
can explicitly select a longer CPU run; previous failed attempts remain in
`prior_attempts` rather than being discarded when verification is resumed.
`SOURCE_UNDERSTANDING_E2E_QUESTIONS` accepts a JSON list of retrieval questions
appropriate to the selected source. The complete one-page benchmark document
`test/benchmark/test_docs/Doc1.pdf` can be used for a short HTTP smoke test with
questions about RAGFlow's purpose. It does not substitute for a long-book test.

## Remaining acceptance work

- Evaluate heading accuracy, text fidelity and retrieval relevance against a
  labeled Vietnamese/English book corpus. Quality scores are heuristic signals;
  `structure_quality=1` is not a claim that every heading is correct.
- A complete HTTP upload → queue → DeepDOC → understanding → embedding → search
  run passed on an 86-page Vietnamese/English industry report on 2026-10-29.
  Long-book text fidelity and bilingual relevance still need labeled evaluation.
- Canonical references are returned, but automatic chapter expansion and PDF
  citation highlighting in chat are separate consumers still to integrate.
- Token limits are soft structural targets. Oversized indivisible elements
  remain flagged; embedding providers may need an explicit long-input policy.
- Registry identity is strict per source revision/hash/schema (ADR 0002).
  Representation-changing updates require an explicit version/revision policy;
  a changed artifact must not silently replace an existing registry binding.
- OpenSearch/Infinity conversion is unit-tested here; their live services and
  OceanBase were not exercised in this review.

## Recorded test outcomes

- 76 focused Python unit tests passed for source understanding, observations,
  registry, task boundaries, retrieval and document validation.
- 88 parser regression tests passed separately. Their import-only test stubs
  needed `common.constants` and `deepdoc.parser.utils` made available.
- 3 frontend parser-config tests passed, including feature-flag persistence.
- Real MySQL/MinIO/Elasticsearch integration passed. An additional check verified
  the 384-dimensional vector mapping; those service-test vectors are synthetic.
- The one-page HTTP smoke test passed with real TEI embeddings from
  `intfloat/multilingual-e5-small`, one whole-source task, 4 canonical elements,
  1 context node and 2 indexed retrieval units. Worker time was 25.97 seconds;
  canonical JSON was 17,945 bytes and gzip storage was 4,778 bytes. Its report,
  canonical artifact, index sample and retrieval results are retained under
  `.runtime/source-understanding-e2e/f82025257f4c4b359c24511e224e0c18/`.
  A Vietnamese query returned 2 hits containing the expected body, page anchors
  and the correct registry reference. Indexed vectors have 384 dimensions.
- Comparing the benchmark's native text with the extracted canonical text
  revealed `AI assistant` → `Al assistant` and `APIs` → `APls`. All heuristic
  quality dimensions still scored 1.0. These valid-Unicode extraction errors
  remain uncorrected and demonstrate the need for multi-backend fidelity checks
  or stronger lexical evidence; the score must not be treated as accuracy.
- The earlier 86-page run exceeded its deadline in automatic table-orientation
  checks. The `book` parser had bypassed the existing rotation setting. After
  fixing that path, the complete run finished in 467.56 seconds: 702 elements,
  157 context nodes and 614 retrieval units. Its report and canonical artifact
  are under `.runtime/source-understanding-e2e/38d57b34e7a547f49e1523f90d35b875/`.
- The real report scored text 0.854, order 0.999, structure 0.983 and overall
  0.941. Lexical diagnostics flagged 3,072 of 33,697 tokens (9.1%), mostly due
  to adjacent Vietnamese words being merged without spaces. It also reported
  136 retrieval units above the soft token target. The 1,998,378-byte canonical
  JSON artifact compressed to 265,593 bytes with gzip.
- Vietnamese and English questions asking for the global 2010–2023 container
  throughput CAGR both retrieved the correct `+3.6%/year` passage on page 4
  (similarity 0.517 and 0.511). A targeted Vietnamese question about deep-water
  ports retrieved a supporting passage from page 4 at rank 3; the equivalent
  English question ranked unrelated chart passages first. Retrieval succeeded,
  but bilingual ranking is not yet consistently accurate. These were retrieval
  results only; no LLM answer generation was configured or tested.
- One inferred heading with a weak boundary initially failed hierarchy building.
  The pipeline now suppresses that heading with a diagnostic rather than
  aborting ingestion. Three focused hierarchy/quality tests pass after the fix.

These are scoped feature/regression tests, not a run of every Python, frontend
or Go test in the repository. No DeepSeek calls were made. Test tooling, model
downloads, caches and generated outputs remain on drive D.
