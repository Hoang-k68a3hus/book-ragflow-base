/** Các trace nhỏ có expected outcomes cho thuật toán dataset merge ở 08. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { mergeWindows } from './merge_windows.mjs';

const cases = (await readFile(new URL('annotation_cases.jsonl', import.meta.url), 'utf8')).trim().split(/\r?\n/).map(JSON.parse);
const clone = structuredClone;
const cp = (s) => Array.from(s);
const size = (s) => cp(s).length;
const slice = (s,a,b) => cp(s).slice(a,b).join('');
const fixture = (id) => clone(cases.find((c) => c.case_id === id));
const spans = (a) => Array.isArray(a.evidence) ? a.evidence : [a.evidence];

function bundleOf(base) {
  const elements = base.input.request.target_segments.map((s) => ({element_id:s.element_id,text_view:s.text_view,text:s.text}));
  return {
    contract_version:base.input.contract_version,
    source:{document_id:'synthetic_document',content_hash:'sha256:'+createHash('sha256').update(elements.map((e)=>e.text).join('\n\n'),'utf8').digest('hex'),source_revision:'fixture-1'},
    target_id:base.input.request.target_id, tasks:clone(base.input.tasks), elements, windows:[],
  };
}
function windowOf(bundle, base, itemId, ranges = null) {
  ranges ??= bundle.elements.map((e) => [e.element_id,0,size(e.text)]);
  const input = clone(base.input), output = clone(base.expected);
  input.item_id = output.item_id = itemId;
  Object.assign(input.source_metadata,bundle.source);
  input.request.context_segments=[];
  input.request.target_segments=[];
  let cursor=0;
  const texts=[];
  for (const [elementId,start,end] of ranges) {
    const full=bundle.elements.find((e)=>e.element_id===elementId);
    if(texts.length)cursor+=2;
    const text=slice(full.text,start,end);
    input.request.target_segments.push({element_id:elementId,text,text_view:full.text_view,element_start:start,element_end:end,request_start:cursor,request_end:cursor+size(text)});
    texts.push(text);cursor+=size(text);
  }
  input.request.text=texts.join('\n\n');
  const partial = ranges.length!==bundle.elements.length || ranges.some(([id,a,b]) => a!==0 || b!==size(bundle.elements.find((e)=>e.element_id===id).text));
  input.target_coverage=partial?'PARTIAL':'COMPLETE';
  input.request.metadata=partial?{
    request_truncated:true,
    truncated_element_ids:ranges.filter(([id,a,b])=>a!==0||b!==size(bundle.elements.find((e)=>e.element_id===id).text)).map(([id])=>id),
    omitted_target_element_ids:bundle.elements.filter((e)=>!ranges.some(([id])=>id===e.element_id)).map((e)=>e.element_id),
    omitted_context_element_ids:[],
  }:{};
  const visible=(e)=>input.request.target_segments.some((s)=>s.element_id===e.element_id&&s.element_start<=e.start_char&&s.element_end>=e.end_char);
  const remap=new Map();
  for(const [field,prefix] of [['mentions','m'],['roles','r']]) {
    output[field]=output[field].filter((a)=>spans(a).every(visible));
    output[field].forEach((a,i)=>{const id=prefix+String(i+1).padStart(3,'0');remap.set(a.id,id);a.id=id;});
  }
  output.links=output.links.filter((l)=>remap.has(l.source_id)&&remap.has(l.target_id)&&l.evidence.every(visible));
  output.links.forEach((l,i)=>{l.id='l'+String(i+1).padStart(3,'0');l.source_id=remap.get(l.source_id);l.target_id=remap.get(l.target_id);});
  output.status=partial?'PARTIAL':'COMPLETE';
  output.issues=partial?[{id:'i001',code:'TRUNCATED_TARGET',evidence:[],note:'Đây là một cửa sổ của target gốc.'}]:[];
  const window={input,output};bundle.windows.push(window);return window;
}

let passed=0;
const run=(name,fn)=>{try{fn();passed++;}catch(error){throw new Error(`${name}: ${error.message}`,{cause:error});}};
run('dedup overlap keeps repeated occurrences and all origins',()=>{
  const base=fixture('c35'),b=bundleOf(base);
  windowOf(b,base,'w01');windowOf(b,base,'w02');
  const r=mergeWindows(b);
  assert.equal(r.merge_status,'READY_FOR_REVIEW');assert.equal(r.mentions.length,2);
  assert.ok(r.mentions.every((m)=>m.origins.length===2));assert.equal(r.local_to_merged.length,4);
});
run('same local ID in disjoint windows maps to different occurrences',()=>{
  const base=fixture('c35'),b=bundleOf(base),id=b.elements[0].element_id;
  windowOf(b,base,'w01',[[id,0,10]]);windowOf(b,base,'w02',[[id,10,size(b.elements[0].text)]]);
  const r=mergeWindows(b);assert.equal(r.merge_status,'READY_FOR_REVIEW');assert.equal(r.mentions.length,2);
  assert.equal(r.local_to_merged[0].local_id,'m001');assert.equal(r.local_to_merged[1].local_id,'m001');
  assert.notEqual(r.local_to_merged[0].merged_id,r.local_to_merged[1].merged_id);
});
run('overlap window exact duplicate is merged but distinct later occurrence stays',()=>{
  const base=fixture('c35'),b=bundleOf(base),id=b.elements[0].element_id;
  windowOf(b,base,'w01',[[id,0,10]]);windowOf(b,base,'w02');
  const r=mergeWindows(b);assert.deepEqual(r.mentions.map((m)=>m.origins.length),[2,1]);
});
run('window input order does not change derived artifact',()=>{
  const base=fixture('c35'),b=bundleOf(base);
  windowOf(b,base,'w02');windowOf(b,base,'w01');
  const original=clone(b);const a=mergeWindows(b);
  assert.deepEqual(b,original);b.windows.reverse();assert.deepEqual(mergeWindows(b),a);
});
function prefixedTranslation() {
  const base=fixture('c37'),prefix='Hà Nội. ',offset=size(prefix),request=base.input.request;
  request.text=prefix+request.text;request.target_segments[0].text=request.text;
  request.target_segments[0].element_end=request.target_segments[0].request_end=size(request.text);
  for(const a of [...base.expected.mentions,...base.expected.links])for(const e of spans(a)){e.start_char+=offset;e.end_char+=offset;}
  const eid=request.element_ids[0];
  base.expected.mentions.unshift({id:'m001',label:'LOCATION',surface:'Hà Nội',evidence:{element_id:eid,text_view:'RAW_TEXT',start_char:0,end_char:6,quoted_text:'Hà Nội'}});
  base.expected.mentions.forEach((m,i)=>m.id='m'+String(i+1).padStart(3,'0'));
  base.expected.links[0].source_id='m003';base.expected.links[0].target_id='m002';
  return {base,offset};
}
run('remap local link IDs after overlap dedup',()=>{
  const {base,offset}=prefixedTranslation(),b=bundleOf(base),id=b.elements[0].element_id;
  windowOf(b,base,'w01');const suffix=windowOf(b,base,'w02',[[id,offset,size(b.elements[0].text)]]);
  assert.equal(suffix.output.links[0].target_id,'m001');
  const r=mergeWindows(b);assert.equal(r.mentions.length,3);assert.equal(r.links.length,1);assert.equal(r.links[0].witnesses.length,2);
  const a=r.local_to_merged.find((x)=>x.item_id==='w01'&&x.local_id==='m002');
  const z=r.local_to_merged.find((x)=>x.item_id==='w02'&&x.local_id==='m001');
  assert.equal(a.merged_id,z.merged_id);assert.equal(r.links[0].target_id,z.merged_id);
});
run('label conflict quarantines endpoints and dependent links',()=>{
  const base=fixture('c37'),b=bundleOf(base);windowOf(b,base,'w01');
  windowOf(b,base,'w02').output.mentions[0].label='PRODUCT';
  const r=mergeWindows(b);assert.equal(r.merge_status,'NEEDS_REVIEW');assert.equal(r.links.length,0);
  assert.equal(r.pending_links.length,2);assert.ok(r.conflicts.some((c)=>c.code==='LABEL_CONFLICT'));
  assert.equal(r.local_to_merged.filter((m)=>m.merged_id===null).length,2);
});
run('overlapping concept boundaries do not choose the longest',()=>{
  const base=fixture('c47'),b=bundleOf(base);windowOf(b,base,'w01');const w=windowOf(b,base,'w02');
  const m=w.output.mentions[0],old=m.evidence,quote='hình tròn';m.surface=quote;
  old.start_char=old.end_char-size(quote);old.quoted_text=quote;
  const r=mergeWindows(b);assert.equal(r.mentions.length,0);assert.equal(r.quarantined_annotations.length,2);
  assert.ok(r.conflicts.some((c)=>c.code==='BOUNDARY_CONFLICT'));
});
run('presence versus absence in a fully visible scope requires review',()=>{
  const base=fixture('c01'),b=bundleOf(base);windowOf(b,base,'w01');const w=windowOf(b,base,'w02');
  w.output.mentions.shift();w.output.mentions[0].id='m001';
  const r=mergeWindows(b);assert.equal(r.merge_status,'NEEDS_REVIEW');assert.equal(r.mentions.length,1);
  assert.ok(r.conflicts.some((c)=>c.code==='PRESENCE_CONFLICT'));
});
run('coverage checks inner gaps rather than only min/max offsets',()=>{
  const base=fixture('c35'),b=bundleOf(base),id=b.elements[0].element_id;
  windowOf(b,base,'w01',[[id,0,6]]);windowOf(b,base,'w02',[[id,10,size(b.elements[0].text)]]);
  const r=mergeWindows(b);assert.equal(r.merge_status,'INCOMPLETE');assert.deepEqual(r.coverage[0].reviewable.gaps,[[6,10]]);
});
run('context cannot fill target coverage',()=>{
  const base=fixture('c35'),b=bundleOf(base),id=b.elements[0].element_id;
  const w=windowOf(b,base,'w01',[[id,0,6]]),text=slice(b.elements[0].text,6,size(b.elements[0].text));
  w.input.request.text+='\n\n'+text;
  w.input.request.context_segments=[{element_id:id,text,text_view:'RAW_TEXT',element_start:6,element_end:size(b.elements[0].text),request_start:8,request_end:8+size(text)}];
  assert.equal(mergeWindows(b).merge_status,'INCOMPLETE');
});
run('task coverage is tracked independently',()=>{
  const base=fixture('c01'),b=bundleOf(base);b.tasks.push('ROLES');windowOf(b,base,'w01');
  const r=mergeWindows(b);assert.equal(r.merge_status,'INCOMPLETE');assert.ok(r.coverage.some((c)=>c.task==='ROLES'&&c.reviewable.gaps.length));
});
run('source revision mismatch is rejected',()=>{
  const base=fixture('c01'),b=bundleOf(base);windowOf(b,base,'w01');windowOf(b,base,'w02').input.source_metadata.source_revision='other';
  assert.throws(()=>mergeWindows(b),/Mixed source source_revision/);
});
run('source hash mismatch is rejected',()=>{
  const base=fixture('c01'),b=bundleOf(base);windowOf(b,base,'w01').input.source_metadata.content_hash='sha256:'+'0'.repeat(64);
  assert.throws(()=>mergeWindows(b),/Mixed source content_hash/);
});
run('duplicate item IDs are rejected rather than picking the last retry',()=>{
  const base=fixture('c01'),b=bundleOf(base);windowOf(b,base,'w01');windowOf(b,base,'w01');
  assert.throws(()=>mergeWindows(b),/Duplicate item_id/);
});
run('mixed raw and normalized text views are rejected',()=>{
  const base=fixture('c01'),b=bundleOf(base);const w=windowOf(b,base,'w01');
  w.input.request.target_segments[0].text_view='NORMALIZED_TEXT';w.output.mentions.forEach((m)=>m.evidence.text_view='NORMALIZED_TEXT');
  assert.throws(()=>mergeWindows(b),/text view/);
});
run('canonical source mismatch cannot be hidden by a matching request quote',()=>{
  const base=fixture('c01'),b=bundleOf(base);windowOf(b,base,'w01');b.elements[0].text='ABC ở Hà Nội.';
  assert.throws(()=>mergeWindows(b),/canonical source/);
});
run('semantic issues survive even if another window supplies the whole text',()=>{
  const base=fixture('c01'),b=bundleOf(base);windowOf(b,base,'w01');const w=windowOf(b,base,'w02');
  w.output.status='ABSTAIN';w.output.mentions=[];w.output.issues=[{id:'i001',code:'OCR_UNREADABLE',evidence:[],note:'Lượt này không đọc được target đáng tin.'}];
  const r=mergeWindows(b);assert.equal(r.merge_status,'NEEDS_REVIEW');assert.equal(r.unresolved_issues.length,1);
});
run('alias versus translation conflicts preserve both witnesses for review',()=>{
  const base=fixture('c37'),b=bundleOf(base);windowOf(b,base,'w01');windowOf(b,base,'w02').output.links[0].type='ALIAS_OF';
  const r=mergeWindows(b);assert.equal(r.links.length,0);assert.equal(r.quarantined_links.length,2);
  assert.ok(r.conflicts.some((c)=>c.code==='LINK_TYPE_CONFLICT'));
});
run('separate windows do not synthesize a relation absent from all windows',()=>{
  const base=fixture('c39'),b=bundleOf(base);
  bundleOf(base).elements.forEach((e,i)=>windowOf(b,base,'w0'+(i+1),[[e.element_id,0,size(e.text)]]));
  const r=mergeWindows(b);assert.equal(r.roles.length,2);assert.equal(r.links.length,0);
});
run('partial role fragments are not concatenated with a full role',()=>{
  const base=fixture('c07'),b=bundleOf(base);windowOf(b,base,'w01');const id=b.elements[0].element_id;
  const w=windowOf(b,base,'w02',[[id,0,20]]),s=w.input.request.target_segments[0];
  w.output.roles=[{id:'r001',label:'DEFINITION',evidence:[{element_id:id,text_view:s.text_view,start_char:0,end_char:20,quoted_text:s.text}]}];
  const r=mergeWindows(b);assert.equal(r.roles.length,0);assert.equal(r.quarantined_annotations.length,2);
  assert.ok(r.conflicts.some((c)=>c.code==='BOUNDARY_CONFLICT'));
});
console.log(`PASS: ${passed} window merge traces; dedup, local IDs, link remapping, conflicts, coverage and source identity.`);
