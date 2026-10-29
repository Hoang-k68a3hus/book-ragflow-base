/** Bản tham chiếu dataset merge của 08; không được nối vào runtime Python. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { validateCase } from './validate_examples.mjs';

const VERSION = 'edu-annotation-1.1-pilot';
const check = (condition, message) => assert.ok(condition, message);
const cp = (text) => Array.from(text);
const slice = (text, start, end) => cp(text).slice(start, end).join('');
const encode = (value) => JSON.stringify(value);
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const digest = (key) => createHash('sha256').update(encode(key), 'utf8').digest('hex');
const spans = (annotation) => Array.isArray(annotation.evidence) ? annotation.evidence : [annotation.evidence];
const spanKey = (e) => [e.element_id, e.text_view, e.start_char, e.end_char, e.quoted_text];
const overlap = (a, b) => a.element_id === b.element_id && a.text_view === b.text_view && a.start_char < b.end_char && b.start_char < a.end_char;
const contains = (outer, inner) => outer.element_id === inner.element_id && outer.text_view === inner.text_view && outer.element_start <= inner.start_char && outer.element_end >= inner.end_char;
const cleanWindow = (w) => w.output.status !== 'ABSTAIN' && w.output.issues.every((i) => i.code === 'TRUNCATED_TARGET');
const originKey = (origin) => encode([origin.item_id, origin.local_id]);
const originSort = (a, b) => cmp(a.item_id, b.item_id) || cmp(a.local_id, b.local_id);

function coverageGaps(intervals, size) {
  const merged = [];
  for (const [start, end] of intervals.sort((a, b) => a[0] - b[0] || a[1] - b[1])) {
    const tail = merged.at(-1);
    if (tail && start <= tail[1]) tail[1] = Math.max(tail[1], end);
    else merged.push([start, end]);
  }
  const gaps = [];
  let cursor = 0;
  for (const [start, end] of merged) { if (start > cursor) gaps.push([cursor, start]); cursor = end; }
  if (cursor < size) gaps.push([cursor, size]);
  return { intervals: merged, gaps };
}

export function mergeWindows(bundle) {
  check(bundle.contract_version === VERSION, 'Unsupported merge contract');
  const source = bundle.source;
  check(typeof source.document_id === 'string' && source.document_id.length > 0, 'Missing canonical document identity');
  check(/^sha256:[0-9a-f]{64}$/.test(source.content_hash), 'Invalid canonical source hash');
  check(source.source_revision === null || typeof source.source_revision === 'string', 'Missing source revision');
  check(typeof bundle.target_id === 'string' && bundle.target_id.length > 0, 'Missing target identity');
  check(Array.isArray(bundle.tasks) && bundle.tasks.length > 0 && new Set(bundle.tasks).size === bundle.tasks.length, 'Invalid merge tasks');
  check(bundle.tasks.every((t) => ['MENTIONS','ROLES','LINKS'].includes(t)) && bundle.tasks.some((t) => t !== 'LINKS'), 'Unsupported merge tasks');
  check(bundle.elements.length > 0 && bundle.windows.length > 0, 'Empty merge input');
  const elements = new Map();
  bundle.elements.forEach((e, index) => {
    check(!elements.has(e.element_id), 'Duplicate element or mixed text views');
    check(['RAW_TEXT','NORMALIZED_TEXT'].includes(e.text_view) && typeof e.text === 'string', 'Invalid canonical text view');
    elements.set(e.element_id, { ...e, index });
  });
  const location = (e) => [elements.get(e.element_id).index, e.start_char, e.end_char];
  const compareEvidence = (a, b) => {
    const x = location(a), y = location(b);
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
  };
  const checkEvidence = (e) => {
    const full = elements.get(e.element_id);
    check(full && full.text_view === e.text_view, 'Evidence view is not canonical');
    check(slice(full.text, e.start_char, e.end_char) === e.quoted_text, 'Canonical evidence mismatch');
    check(e.start_char >= 0 && e.end_char <= cp(full.text).length, 'Evidence outside canonical text');
  };
  const sourceKey = [VERSION, source.document_id, source.content_hash, source.source_revision, bundle.target_id];
  const windows = bundle.windows.map((w) => ({ ...w })).sort((a, b) => cmp(a.input.item_id, b.input.item_id));
  check(new Set(windows.map((w) => w.input.item_id)).size === windows.length, 'Duplicate item_id; select one retry before merge');
  const groups = new Map(), local = new Map(), originalIssues = [];
  for (const w of windows) {
    validateCase({ input: w.input, expected: w.output });
    const { input, output } = w;
    check(input.contract_version === VERSION && output.contract_version === VERSION, 'Mixed guideline versions');
    check(input.request.target_id === bundle.target_id, 'Mixed target identities');
    check(input.request.target_kind === windows[0].input.request.target_kind, 'Mixed target kinds');
    for (const key of ['document_id','content_hash','source_revision']) check(input.source_metadata[key] === source[key], `Mixed source ${key}`);
    check(input.tasks.every((task) => bundle.tasks.includes(task)), 'Unexpected task');
    check(input.request.element_ids.every((id) => elements.has(id)), 'Element outside canonical target');
    let previous = null;
    for (const s of input.request.target_segments) {
      const full = elements.get(s.element_id);
      check(full && full.text_view === s.text_view, 'Mixed or missing target text view');
      check(s.element_start >= 0 && s.element_end <= cp(full.text).length, 'Target slice outside canonical text');
      check(slice(full.text, s.element_start, s.element_end) === s.text, 'Target text disagrees with canonical source');
      check(previous === null || elements.get(previous).index < full.index, 'Target elements not in canonical order');
      previous = s.element_id;
    }
    for (const key of ['mentions','roles','links','issues']) for (const a of output[key]) spans(a).forEach(checkEvidence);
    w.candidateIds = new Set();
    for (const [field, task, prefix] of [['mentions','MENTIONS','ma_'], ['roles','ROLES','ra_']]) {
      for (const a of output[field]) {
        const evidence = spans(a);
        for (let i = 1; i < evidence.length; i++) check(compareEvidence(evidence[i - 1], evidence[i]) <= 0, 'Noncanonical evidence order');
        const evKey = field === 'mentions' ? spanKey(a.evidence) : evidence.map(spanKey);
        const key = [sourceKey, task, a.label, evKey];
        const id = prefix + digest(key);
        const origin = { item_id: input.item_id, local_id: a.id };
        let group = groups.get(id);
        if (!group) {
          group = { id, key, task, annotation: { ...structuredClone(a), id }, origins: [], blocked: false };
          groups.set(id, group);
        }
        check(encode(group.key) === encode(key), 'Annotation hash collision');
        group.origins.push(origin);
        local.set(originKey(origin), group);
        w.candidateIds.add(id);
      }
    }
    for (const issue of output.issues) originalIssues.push({ item_id: input.item_id, issue: structuredClone(issue) });
  }
  const conflicts = [], conflictKeys = new Set();
  const conflict = (code, candidateIds, items = []) => {
    const ids = [...candidateIds].sort(cmp), itemIds = [...items].sort(cmp);
    const key = encode([code, ids, itemIds]);
    if (!conflictKeys.has(key)) { conflictKeys.add(key); conflicts.push({ code, candidate_ids: ids, item_ids: itemIds }); }
    ids.forEach((id) => { if (groups.has(id)) groups.get(id).blocked = true; });
  };
  const candidates = [...groups.values()].sort((a, b) => compareEvidence(spans(a.annotation)[0], spans(b.annotation)[0]) || cmp(a.id,b.id));
  for (let i = 0; i < candidates.length; i++) for (let j = i + 1; j < candidates.length; j++) {
    const a = candidates[i], b = candidates[j];
    if (a.task !== b.task || !spans(a.annotation).some((x) => spans(b.annotation).some((y) => overlap(x,y)))) continue;
    const sameExtent = encode(spans(a.annotation).map(spanKey)) === encode(spans(b.annotation).map(spanKey));
    conflict(sameExtent ? 'LABEL_CONFLICT' : 'BOUNDARY_CONFLICT', [a.id,b.id]);
  }
  for (const group of candidates) for (const w of windows) {
    if (!w.input.tasks.includes(group.task) || !cleanWindow(w) || w.candidateIds.has(group.id)) continue;
    if (spans(group.annotation).every((e) => w.input.request.target_segments.some((s) => contains(s,e))))
      conflict('PRESENCE_CONFLICT', [group.id], [w.input.item_id]);
  }
  const mapping = [...local.entries()].map(([key, group]) => {
    const [item_id, local_id] = JSON.parse(key);
    return { item_id, local_id, candidate_id: group.id, merged_id: group.blocked ? null : group.id };
  }).sort(originSort);
  const links = new Map(), pendingLinks = [];
  for (const w of windows) for (const original of w.output.links) {
    const origin = { item_id: w.input.item_id, local_id: original.id };
    const a = local.get(encode([origin.item_id, original.source_id]));
    const b = local.get(encode([origin.item_id, original.target_id]));
    check(a && b, 'Unresolved local link endpoint');
    if (a.blocked || b.blocked) { pendingLinks.push({ ...origin, reason:'QUARANTINED_ENDPOINT' }); continue; }
    const key = [sourceKey, original.type, a.id, b.id];
    const id = 'la_' + digest(key);
    if (!links.has(id)) links.set(id, { id, type:original.type, source_id:a.id, target_id:b.id, witnesses:[], key, blocked:false });
    const link = links.get(id);
    check(encode(link.key) === encode(key), 'Link hash collision');
    link.witnesses.push({ ...origin, evidence:structuredClone(original.evidence) });
  }
  const equivalent = [...links.values()].filter((l) => ['ALIAS_OF','TRANSLATION_OF'].includes(l.type));
  for (let i = 0; i < equivalent.length; i++) for (let j = i + 1; j < equivalent.length; j++) {
    const a = equivalent[i], b = equivalent[j];
    if (a.source_id === b.source_id && a.target_id === b.target_id && a.type !== b.type) {
      a.blocked = b.blocked = true;
      conflict('LINK_TYPE_CONFLICT', [a.id,b.id]);
    }
  }
  const coverage = [];
  for (const task of [...bundle.tasks].sort(cmp)) for (const full of bundle.elements) {
    const supplied = [], reviewable = [];
    for (const w of windows) if (w.input.tasks.includes(task)) {
      for (const s of w.input.request.target_segments.filter((s) => s.element_id === full.element_id)) {
        supplied.push([s.element_start,s.element_end]);
        if (cleanWindow(w)) reviewable.push([s.element_start,s.element_end]);
      }
    }
    coverage.push({ task, element_id:full.element_id, text_view:full.text_view,
      supplied:coverageGaps(supplied,cp(full.text).length), reviewable:coverageGaps(reviewable,cp(full.text).length) });
  }
  const incomplete = coverage.some((c) => c.reviewable.gaps.length > 0);
  const unresolvedIssues = originalIssues.filter((i) => i.issue.code !== 'TRUNCATED_TARGET' || incomplete);
  const decorate = (g) => ({ ...g.annotation, origins:g.origins.sort(originSort) });
  const presentLink = ({ key, blocked, ...l }) => ({ ...l, witnesses:l.witnesses.sort(originSort) });
  return {
    contract_version:VERSION, source:structuredClone(source), target_id:bundle.target_id,
    merge_status:conflicts.length || unresolvedIssues.some((i) => i.issue.code !== 'TRUNCATED_TARGET') ? 'NEEDS_REVIEW' : incomplete ? 'INCOMPLETE' : 'READY_FOR_REVIEW',
    mentions:candidates.filter((g) => !g.blocked && g.task === 'MENTIONS').map(decorate),
    roles:candidates.filter((g) => !g.blocked && g.task === 'ROLES').map(decorate),
    links:[...links.values()].filter((l) => !l.blocked).map(presentLink).sort((a,b) => cmp(a.id,b.id)),
    local_to_merged:mapping,
    quarantined_annotations:candidates.filter((g) => g.blocked).map(decorate),
    quarantined_links:[...links.values()].filter((l) => l.blocked).map(presentLink).sort((a,b) => cmp(a.id,b.id)),
    pending_links:pendingLinks.sort(originSort),
    conflicts:conflicts.sort((a,b) => cmp(encode(a),encode(b))),
    coverage, input_issues:originalIssues, unresolved_issues:unresolvedIssues,
  };
}
