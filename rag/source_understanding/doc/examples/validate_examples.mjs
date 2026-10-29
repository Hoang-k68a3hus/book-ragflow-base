/** Kiểm chứng fixture của guideline; chỉ hỗ trợ các keyword schema dùng ở đây. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const readJSON = async (relative) => JSON.parse(await readFile(new URL(relative, import.meta.url), 'utf8'));
const inputSchema = await readJSON('../schemas/input.schema.json');
const outputSchema = await readJSON('../schemas/output.schema.json');
const cases = (await readFile(new URL('annotation_cases.jsonl', import.meta.url), 'utf8'))
  .trim().split(/\r?\n/).map(JSON.parse);
const length = (text) => Array.from(text).length;
const slice = (text, start, end) => Array.from(text).slice(start, end).join('');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const check = (condition, message) => assert.ok(condition, message);

// Không phải JSON Schema engine tổng quát. Keyword lạ bị từ chối, không bỏ qua.
const supported = new Set([
  '$schema', '$defs', '$ref', 'title', 'type', 'const', 'enum', 'properties',
  'required', 'additionalProperties', 'items', 'minItems', 'maxItems',
  'uniqueItems', 'contains', 'minLength', 'maxLength', 'pattern', 'minimum',
  'maximum', 'allOf', 'if', 'then',
]);
function auditSchema(schema) {
  for (const key of Object.keys(schema)) check(supported.has(key), `Unsupported schema keyword: ${key}`);
  for (const child of Object.values(schema.properties ?? {})) auditSchema(child);
  for (const child of Object.values(schema.$defs ?? {})) auditSchema(child);
  for (const child of schema.allOf ?? []) auditSchema(child);
  for (const key of ['items', 'contains', 'if', 'then']) if (schema[key]) auditSchema(schema[key]);
}
function validateSchema(value, schema, root = schema, loc = '$') {
  if (schema.$ref) {
    check(schema.$ref.startsWith('#/$defs/'), `${loc}: unexpected reference`);
    const target = root.$defs[schema.$ref.slice('#/$defs/'.length)];
    check(target, `${loc}: unresolved reference`);
    validateSchema(value, target, root, loc);
  }
  const matches = (candidate, subschema) => {
    try { validateSchema(candidate, subschema, root, loc); return true; }
    catch (error) { if (error instanceof assert.AssertionError) return false; throw error; }
  };
  if (schema.type) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    check(types.some((type) => {
      if (type === 'null') return value === null;
      if (type === 'integer') return Number.isInteger(value);
      if (type === 'array') return Array.isArray(value);
      if (type === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value);
      return typeof value === type;
    }), `${loc}: incorrect type`);
  }
  if ('const' in schema) check(same(value, schema.const), `${loc}: const mismatch`);
  if (schema.enum) check(schema.enum.some((entry) => same(value, entry)), `${loc}: unknown enum value`);
  if (typeof value === 'string') {
    if ('minLength' in schema) check(length(value) >= schema.minLength, `${loc}: string too short`);
    if ('maxLength' in schema) check(length(value) <= schema.maxLength, `${loc}: string too long`);
    if (schema.pattern) check(new RegExp(schema.pattern, 'u').test(value), `${loc}: pattern mismatch`);
  }
  if (typeof value === 'number') {
    if ('minimum' in schema) check(value >= schema.minimum, `${loc}: below minimum`);
    if ('maximum' in schema) check(value <= schema.maximum, `${loc}: above maximum`);
  }
  if (Array.isArray(value)) {
    if ('minItems' in schema) check(value.length >= schema.minItems, `${loc}: too few items`);
    if ('maxItems' in schema) check(value.length <= schema.maxItems, `${loc}: too many items`);
    if (schema.uniqueItems) check(new Set(value.map(JSON.stringify)).size === value.length, `${loc}: duplicate items`);
    if (schema.items) value.forEach((entry, i) => validateSchema(entry, schema.items, root, `${loc}[${i}]`));
    if (schema.contains) check(value.some((entry) => matches(entry, schema.contains)), `${loc}: contains mismatch`);
  }
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    for (const key of schema.required ?? []) check(Object.hasOwn(value, key), `${loc}: missing ${key}`);
    for (const [key, entry] of Object.entries(value)) {
      if (schema.properties?.[key]) validateSchema(entry, schema.properties[key], root, `${loc}.${key}`);
      else if (schema.additionalProperties === false) check(false, `${loc}: extra property ${key}`);
    }
  }
  for (const child of schema.allOf ?? []) validateSchema(value, child, root, loc);
  if (schema.if && matches(value, schema.if) && schema.then) validateSchema(value, schema.then, root, loc);
}

const position = (request, evidence) => {
  const segment = request.target_segments.find((s) => s.element_id === evidence.element_id && s.text_view === evidence.text_view);
  check(segment, 'Evidence must belong to a target segment');
  return segment.request_start + evidence.start_char - segment.element_start;
};
function validateEvidence(request, evidence) {
  const segment = request.target_segments.find((s) => s.element_id === evidence.element_id && s.text_view === evidence.text_view);
  check(segment, 'Evidence outside target or wrong text view');
  check(evidence.start_char >= segment.element_start && evidence.end_char <= segment.element_end, 'Evidence outside element slice');
  check(evidence.end_char > evidence.start_char, 'Empty or reversed evidence');
  check(slice(segment.text, evidence.start_char - segment.element_start, evidence.end_char - segment.element_start) === evidence.quoted_text, 'Quote/offset mismatch');
}
function noOverlap(evidences, message) {
  for (let i = 0; i < evidences.length; i++) for (let j = i + 1; j < evidences.length; j++) {
    const a = evidences[i], b = evidences[j];
    if (a.element_id === b.element_id && a.text_view === b.text_view)
      check(a.end_char <= b.start_char || b.end_char <= a.start_char, message);
  }
}
export function validateCase(item) {
  const { input, expected: output } = item;
  validateSchema(input, inputSchema);
  validateSchema(output, outputSchema);
  const request = input.request;
  check(output.item_id === input.item_id && output.target_id === request.target_id, 'Output identity mismatch');
  check(request.text.trim().length > 0, 'Blank request text');
  check(input.tasks.some((task) => task !== 'LINKS'), 'LINKS needs an annotation task');
  if (request.metadata.request_truncated === true)
    for (const key of ['truncated_element_ids','omitted_target_element_ids','omitted_context_element_ids'])
      check(Array.isArray(request.metadata[key]), 'Truncation must identify affected scopes');
  const segments = [...request.target_segments, ...request.context_segments].sort((a, b) => a.request_start - b.request_start);
  let previousEnd = 0;
  for (const segment of segments) {
    check(segment.request_start >= previousEnd, 'Input segments overlap');
    check(segment.request_end <= length(request.text), 'Segment outside request');
    check(segment.element_end - segment.element_start === length(segment.text), 'Element range length mismatch');
    check(segment.request_end - segment.request_start === length(segment.text), 'Request range length mismatch');
    check(slice(request.text, segment.request_start, segment.request_end) === segment.text, 'Input segment text mismatch');
    previousEnd = segment.request_end;
  }
  const targetIds = request.target_segments.map((s) => s.element_id);
  check(new Set(targetIds).size === targetIds.length, 'Duplicate target segment element');
  check(targetIds.every((id) => request.element_ids.includes(id)), 'Target membership mismatch');
  if (request.target_kind === 'ELEMENT') {
    check(targetIds.length === 1 && request.element_ids.length === 1, 'ELEMENT requires one member');
    check(request.target_id === targetIds[0] && request.element_ids[0] === targetIds[0], 'ELEMENT identity mismatch');
  }
  for (const [task, field] of [['MENTIONS', 'mentions'], ['ROLES', 'roles'], ['LINKS', 'links']])
    if (!input.tasks.includes(task)) check(output[field].length === 0, `Disabled task ${task} has annotations`);
  const targetPartial = input.target_coverage === 'PARTIAL' ||
    (request.metadata.truncated_element_ids ?? []).length > 0 ||
    (request.metadata.omitted_target_element_ids ?? []).length > 0;
  if (targetPartial) {
    check(output.status !== 'COMPLETE', 'Incomplete target marked COMPLETE');
    check(output.issues.some((issue) => issue.code === 'TRUNCATED_TARGET'), 'Missing truncation issue');
  }
  for (const [field, prefix] of [['mentions','m'], ['roles','r'], ['links','l'], ['issues','i']]) {
    output[field].forEach((annotation, i) => {
      check(annotation.id === prefix + String(i + 1).padStart(3, '0'), 'Annotation IDs must be sequential');
      const spans = field === 'mentions' ? [annotation.evidence] : annotation.evidence;
      spans.forEach((evidence) => validateEvidence(request, evidence));
      check(new Set(spans.map(JSON.stringify)).size === spans.length, 'Duplicate evidence');
      for (let n = 1; n < spans.length; n++) check(position(request, spans[n - 1]) <= position(request, spans[n]), 'Unsorted evidence');
    });
  }
  for (const mention of output.mentions) check(mention.surface === mention.evidence.quoted_text, 'Surface must equal quote');
  noOverlap(output.mentions.map((m) => m.evidence), 'Mention overlap');
  noOverlap(output.roles.flatMap((r) => r.evidence), 'Role overlap');
  for (const field of ['mentions','roles']) {
    const evidenceOf = (a) => field === 'mentions' ? a.evidence : a.evidence[0];
    for (let n = 1; n < output[field].length; n++) check(position(request, evidenceOf(output[field][n - 1])) <= position(request, evidenceOf(output[field][n])), 'Annotations not in source order');
  }
  let emptyIssueSeen = false;
  let previousIssuePosition = -1;
  for (const issue of output.issues) {
    if (!issue.evidence.length) { emptyIssueSeen = true; continue; }
    check(!emptyIssueSeen, 'Located issue must precede unlocated issues');
    const current = position(request, issue.evidence[0]);
    check(current >= previousIssuePosition, 'Issues not in source order');
    previousIssuePosition = current;
  }
  for (const role of output.roles) check(new Set(role.evidence.map((e) => e.element_id)).size === role.evidence.length, 'Role has disjoint evidence in one element');
  const mentions = new Map(output.mentions.map((m) => [m.id, m]));
  const roles = new Map(output.roles.map((r) => [r.id, r]));
  const keys = new Set();
  const equivalentPairs = new Set();
  let previousLinkKey = '';
  for (const link of output.links) {
    check(link.source_id !== link.target_id, 'Self link');
    const key = `${link.type}:${link.source_id}:${link.target_id}`;
    check(key >= previousLinkKey, 'Links not sorted'); previousLinkKey = key;
    check(!keys.has(key), 'Duplicate link'); keys.add(key);
    const sm = mentions.get(link.source_id), tm = mentions.get(link.target_id);
    const sr = roles.get(link.source_id), tr = roles.get(link.target_id);
    if (['ALIAS_OF', 'TRANSLATION_OF'].includes(link.type)) {
      check(sm && tm, 'Alias/translation endpoints must be mentions');
      check(position(request, sm.evidence) > position(request, tm.evidence), 'Alias/translation direction must be later to earlier');
      const pair = `${link.source_id}:${link.target_id}`;
      check(!equivalentPairs.has(pair), 'Alias and translation cannot duplicate one pair');
      equivalentPairs.add(pair);
    } else if (link.type === 'ANSWER_TO') check(sr?.label === 'ANSWER' && ['EXERCISE','QUESTION'].includes(tr?.label), 'Invalid ANSWER_TO endpoints');
    else if (link.type === 'EXAMPLE_OF') check(sr?.label === 'EXAMPLE' && ['CONCEPT','METHOD','NAMED_PRINCIPLE'].includes(tm?.label), 'Invalid EXAMPLE_OF endpoints');
    else if (link.type === 'PROOF_OF') check(sr?.label === 'PROOF' && tr?.label === 'THEOREM', 'Invalid PROOF_OF endpoints');
    const endpointSpans = [sm?.evidence, tm?.evidence, ...(sr?.evidence ?? []), ...(tr?.evidence ?? [])].filter(Boolean);
    for (const endpoint of endpointSpans)
      check(link.evidence.some((e) => e.element_id === endpoint.element_id && e.text_view === endpoint.text_view && e.start_char <= endpoint.start_char && e.end_char >= endpoint.end_char), 'Link evidence must contain both endpoints');
  }
}

auditSchema(inputSchema); auditSchema(outputSchema);
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
check(new Set(cases.map((item) => item.case_id)).size === cases.length, 'Duplicate fixture case ID');
for (const item of cases) {
  try { validateCase(item); }
  catch (error) { throw new Error(`${item.case_id}: ${error.message}`, { cause: error }); }
}
const coverage = (field, key) => new Set(cases.flatMap((c) => c.expected[field].map((a) => a[key])));
for (const [field, key] of [['mentions','label'], ['roles','label'], ['links','type']])
  for (const label of outputSchema.properties[field].items.properties[key].enum)
    check(coverage(field, key).has(label), `No fixture for ${label}`);

// Các lỗi cố tình đưa vào phải bị từ chối: tránh validator luôn trả thành công.
const corruptions = [
  ['c01', (c) => { c.expected.mentions[0].label = 'UNKNOWN_LABEL'; }],
  ['c01', (c) => { c.expected.mentions[0].confidence = 0.99; }],
  ['c01', (c) => { c.expected.mentions[0].evidence.end_char++; }],
  ['c26', (c) => { c.expected.mentions[0].evidence.start_char++; c.expected.mentions[0].evidence.end_char++; }],
  ['c25', (c) => { c.expected.status = 'COMPLETE'; c.expected.issues = []; }],
  ['c18', (c) => { const s = c.input.request.context_segments[0]; c.expected.mentions = [{id:'m001',label:'CONCEPT',surface:s.text,evidence:{element_id:s.element_id,text_view:s.text_view,start_char:0,end_char:length(s.text),quoted_text:s.text}}]; }],
  ['c01', (c) => { const m = structuredClone(c.expected.mentions[0]); m.id='m002'; c.expected.mentions=[c.expected.mentions[0],m]; }],
  ['c37', (c) => { c.expected.links[0].target_id='m999'; }],
  ['c37', (c) => { [c.expected.links[0].source_id,c.expected.links[0].target_id]=[c.expected.links[0].target_id,c.expected.links[0].source_id]; }],
  ['c01', (c) => { c.input.tasks=['ROLES']; }],
  ['c07', (c) => { const r = structuredClone(c.expected.roles[0]); r.id='r002'; c.expected.roles.push(r); }],
  ['c01', (c) => { c.expected.status='ABSTAIN'; c.expected.issues=[]; }],
  ['c42', (c) => { c.input.request.metadata.truncated_element_ids='not-an-array'; }],
  ['c42', (c) => { delete c.input.request.metadata.omitted_context_element_ids; }],
  ['c37', (c) => { c.expected.links[0].evidence=[structuredClone(c.expected.mentions[0].evidence)]; }],
  ['c37', (c) => { const l=structuredClone(c.expected.links[0]); l.type='ALIAS_OF'; c.expected.links.unshift(l); c.expected.links.forEach((x,i)=>x.id='l'+String(i+1).padStart(3,'0')); }],
  ['c01', (c) => { c.input.tasks=['LINKS']; c.expected.mentions=[]; }],
  ['c01', (c) => { c.input.source_metadata.grade=5; }],
  ['c01', (c) => { c.input.request.target_segments[0].request_end++; }],
  ['c29', (c) => { c.expected.mentions[0].evidence.text_view='RAW_TEXT'; }],
  ['c01', (c) => { c.expected.contract_version='edu-annotation-1.0'; }],
];
for (const [id, mutate] of corruptions) {
  const item = structuredClone(cases.find((c) => c.case_id === id));
  mutate(item);
  assert.throws(() => validateCase(item), assert.AssertionError, `Failed to reject corruption based on ${id}`);
}
console.log(`PASS: ${cases.length} fixtures; 9 mention labels, 10 role labels, 5 link types; ${corruptions.length} corruptions rejected.`);
console.log(`Source: ${fileURLToPath(new URL('annotation_cases.jsonl', import.meta.url))}`);
}
