# PDF font-aware glyph inspection and recovery

## Scope

This change adds a narrowly scoped recovery path to the native-text PDF parser. Poppler remains the primary extractor. pypdf inspects source font resources and content codes on candidate pages; PDFium is called only for selected pages. OCR and language-model guesses are not used.

The parser keeps the existing `DeepDocParseResult`, `DeepDocObservation`, page geometry, source IDs, order, and manual `glyph_corrections` contract. A font correction is applied only after an evidence gate succeeds. Manual corrections take precedence.

## Flow

```text
PDF bytes
  -> Poppler text and layout
  -> text anomaly scan and candidate-page selection
  -> pypdf font/CMap/content-code inspection on candidate pages
  -> PDFium character text, font name, and bounding boxes on selected pages
  -> exact source-code/font/geometry/text-span alignment
  -> VERIFIED span proposal, or diagnostic with original text retained
  -> DeepDoc observations with source locations and correction provenance
```

`PdfGlyphInspector.inspect()` can inspect all pages when called directly. `PdfTextParser` scopes it to pages containing explicit extraction sentinels or the known `6 =` candidate shape. The `6 =` shape alone is not classified as an error and is never sufficient to edit text.

## Confirmed real-PDF case

A local, user-supplied 141-page PDF reproduced the corruption. The PDF is not included in this repository. Its local SHA-256 begins `2e23c06a8738`.

On the affected pages, the source mapping shows a Type1 subset math font with no `/ToUnicode` map. For the confirmed occurrences, character code `0x36` is listed in the font encoding Differences as `/negationslash`; Poppler and PDFium both surfaced the component as `6`. The character code, glyph name, Unicode mapping, CID, and GID are recorded separately; CID/GID are marked unavailable for this simple font rather than inferred.

The adjacent equals glyph is independently supported by an embedded font resource whose encoding maps a code to `/equal`. On the affected lines, PDFium's `negationslash` and equals boxes overlap and share a center. The exact Poppler line contains the same number of `6 =` candidates as geometrically paired source glyphs, in the same order. Backend agreement is not treated as ground truth: the two backends agree on `6`, which is visibly wrong in this PDF.

The rendered pages 31, 37, 38, 54, 55, 111, and 113 were reviewed locally. The corrected symbols are visibly `≠`, including the page-37 expression `α ≠ 0, α ≠ 1`. Rendered images and the source PDF remain local under `.runtime` and are not committed.

Across the document, the baseline parser emitted 51 `6 =` occurrences. The new parser corrected 12 spans in 9 observations; 39 occurrences remain unchanged. All 9 changed observations carry `font_glyph_name_negationslash_overlaid_equal` provenance. No other observation text changed in the baseline/current comparison.

## Detection and verification policy

The text scanner reports Unicode replacement characters, private-use values, unexpected C0/C1 control characters, and CID placeholder strings. It does not treat valid mathematical Unicode or ASCII `6=` as an anomaly. These markers are diagnostics, not confidence scores.

The current automatic repair is limited to the confirmed construction:

1. pypdf reads the raw PDF text code and the page font resource that paints it.
2. The source font's Encoding Differences explicitly name the code `/negationslash`.
3. PDFium characters can be aligned one-to-one to that font's source codes using an unambiguous font name and mapping on that page.
4. PDFium reports a separate `=` character from an embedded font mapping that explicitly names `/equal`.
5. The glyph boxes overlap by at least 0.5 of the smaller glyph's area and their centers coincide within the configured tolerance.
6. Both boxes fall inside exactly one Poppler line and block. Every `6\s*=` candidate on that line has a matching geometry pair; repeated candidates are paired left-to-right.
7. The correction is bound to the PDF SHA-256, page, zero-based Poppler block, exact original text, exact spans, and source-font fingerprints.

If resource selection, font identity, line geometry, candidate counts, or text alignment is ambiguous, the result is `SUSPECT` or `CONFLICT`, and the original text remains. A literal `6=` with no `/negationslash` source mapping is not changed. A valid literal `6=` on the same line as an overlay makes the line ambiguous and prevents automatic repair.

No global replacement, OCR, language model, font-name-only cache, or cross-document mapping reuse is present.

## Provenance

For every automatic correction, the observation keeps its original `source_id`, order, page, and Poppler bounding box. `attributes.glyph_provenance` stores the original and corrected block text, method, verification status, PDF hash, page/block, exact span offsets, font fingerprints, and evidence from both backends. Evidence includes source character code, glyph name, embedded-font hash, PDFium font/text, normalized glyph boxes, overlap ratio, and source location resolution.

The parser also emits typed `DeepDocDiagnostic` records for suspected mappings, unresolved/conflicting alignments, backend failures, and successful repairs. Manual verified corrections still use the existing hash/page/block/original-text validation and take precedence over font proposals.

## Performance measurement

Measured locally with Python 3.13.15, Poppler 26.09.0, the same 141-page PDF, and the parser at base commit `aa6b4e3fd4e9c11eadd81816908c321383453cd8` versus this implementation. Each parser had three in-process warm trials; p95 uses nearest rank. Peak RSS is the sampled process RSS, so it includes the same Python process and imported modules.

| Metric | Baseline | Font-aware parser | Change |
| --- | ---: | ---: | ---: |
| Warm latency p50 | 4,785.8 ms | 11,594.3 ms | +142.3% |
| Warm latency p95 | 4,834.7 ms | 12,638.7 ms | +161.4% |
| Peak RSS p50 | 173,473,792 B | 173,506,560 B | +0.02% |
| Peak RSS p95 | 175,874,048 B | 180,363,264 B | +2.55% |

One instrumented parser run took 13,755 ms: Poppler 2,052 ms, font inspection 1,683 ms, PDFium 4,281 ms, verification 223 ms, and 9 span applications under 1 ms total. That run inspected 29 candidate pages and sent 27 pages to PDFium. It is a stage breakdown from one run, not a stage p50/p95.

The added work is limited to candidate pages, but this corpus contains many `6 =` candidates, so latency is still about 2.4 times baseline. This is a measured regression and remains a performance limitation; it is not presented as an improvement. The first-call samples were not run with OS caches cleared and are not used for the warm comparison.

## Tests and real regression

Focused tests cover:

- standard and subset font names, missing and malformed ToUnicode maps, exact CMap code-space ranges, duplicate mapping conflicts, and distinct character-code/CID/GID states;
- PUA, replacement, control, and CID-placeholder diagnostics without flagging valid math or literal `6=`;
- one and repeated overlay occurrences, mixed literal/overlay text, conflicting font resources, backend absence, geometry mismatch, stale PDF hashes, rotated/cropped coordinates, ligature segmentation, and Unicode normalization;
- preservation of manual correction precedence, source IDs, observation order, raw text, and provenance.

The real PDF was parsed with native Poppler and page-selective PDFium, and its relevant pages were rendered and inspected locally. The PDF is intentionally not a committed fixture. A successful test run validates these covered cases; it does not establish that every PDF font encoding can be decoded.

## Known limitations

- Automatic recovery currently handles only `/negationslash` geometrically overlaid on a separately mapped `/equal`. Other broken CMaps and glyph compositions are reported or retained unchanged.
- PUA and control-character findings are not automatically rewritten. They remain `SUSPECT`; only replacement-character and CID-placeholder findings recommend page-level secondary verification. The current detector found 531 PUA/control occurrences on 72 pages in the real PDF (156 PUA, 375 controls). Those occurrences were not adjudicated as errors.
- Poppler exposes text-line/block geometry here, not per-character boxes. Generic text anomalies therefore have block/text-character offsets, not fabricated character boxes.
- Page content is inspected through the page's direct font resources; nested Form XObject font scopes and all possible CMap operators are not fully handled.
- PDFium exposes a font name rather than the originating PDF resource ID. If same-name resources have different mappings, the verifier reports a conflict and does not repair.
- PDFium is optional. If it is missing or fails on a selected page, native text is preserved and a diagnostic is emitted.
- `Plain Text` does not invoke OCR. No OCR-on-scan behavior was added or evaluated.
- The 2.4x warm latency on the measured corpus needs further optimization before claiming broad throughput suitability.
