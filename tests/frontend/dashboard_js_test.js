// Node-based behavior tests for the dashboard's embedded frontend JS.
// Usage: node dashboard_js_test.js <path/to/dashboard.html>
// Loads the page's <script> block in a sandbox with stubbed browser globals,
// then asserts rendering behavior. Exits non-zero on the first failure.
const fs = require('fs');
const vm = require('vm');

const htmlPath = process.argv[2];
if (!htmlPath) {
  console.error('usage: node dashboard_js_test.js <path/to/dashboard.html>');
  process.exit(2);
}
const html = fs.readFileSync(htmlPath, 'utf8');
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) {
  console.error('FAIL: no <script> block found in ' + htmlPath);
  process.exit(2);
}

const noopEl = () => ({
  innerHTML: '', className: '', value: '', style: {}, textContent: '',
  setAttribute() {}, appendChild() {}, addEventListener() {}, querySelectorAll() { return []; },
});
const sandbox = {
  console,
  document: {
    getElementById: () => noopEl(),
    addEventListener: () => {},
    createElement: () => noopEl(),
    querySelectorAll: () => [],
    body: noopEl(),
  },
  fetch: () => new Promise(() => {}), // never resolves; refresh() becomes a no-op
  setInterval: () => 0,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
};
vm.createContext(sandbox);
vm.runInContext(match[1], sandbox);

let failures = 0;
function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); failures++; return; }
  console.log('ok -', msg);
}

// --- renderJsonTree: nested object ---
const tree = vm.runInContext(`renderJsonTree({
  model: 'gpt-4o',
  n: 42, ok: true, nothing: null, text: 'hello <world> & "friends"',
  usage: { prompt_tokens: 10, completion_tokens: 20, nested: { deep: { deeper: [1, 2, 3] } } },
  choices: [{ message: { role: 'assistant', content: 'hi' } }, { message: { role: 'user' } }],
  empty_obj: {}, empty_arr: [],
})`, sandbox);

assert(tree.startsWith('<div class="json-tree">'), 'tree root rendered');
assert(tree.includes('json-copy-src'), 'small trees keep hidden copy source');
assert(tree.includes('j-node collapsed'), 'nodes at depth >= 2 are collapsed');
assert(tree.includes('&#x2026;'), 'collapsed preview ellipsis present');
assert(tree.includes('3 keys'), 'key count label present');
assert(tree.includes('2 items'), 'array item count label present');
assert(tree.includes('&#x2026; 3 items ]'), 'array preview label correct');
assert(!tree.includes('<world>'), 'HTML in string values is escaped');
assert(tree.includes('&lt;world&gt;'), 'escaped HTML entities present');
assert(tree.includes('j-null">null'), 'null rendering');
assert(tree.includes('j-bool">true'), 'boolean rendering');
assert(tree.includes('j-num">42'), 'number rendering');
assert(tree.includes('{}'), 'empty object renders inline');
assert(tree.includes('[]'), 'empty array renders inline');

// --- renderJsonTree: non-object fallbacks ---
const str = vm.runInContext(`renderJsonTree('plain string body')`, sandbox);
assert(str === '<pre>plain string body</pre>', 'string body falls back to pre');
const nul = vm.runInContext(`renderJsonTree(null)`, sandbox);
assert(nul.includes('null'), 'null body falls back');

// --- expand depth: root and depth 1 expanded, depth 2 collapsed ---
const depthHtml = vm.runInContext(`renderJsonTree({a: {b: {c: 1}}})`, sandbox);
const expandedCount = (depthHtml.match(/<div class="j-node">/g) || []).length;
const collapsedCount = (depthHtml.match(/j-node collapsed/g) || []).length;
assert(expandedCount === 2, 'depth 0 and 1 nodes expanded (got ' + expandedCount + ')');
assert(collapsedCount === 1, 'depth 2 node collapsed (got ' + collapsedCount + ')');

// --- esc: HTML escaping helper ---
assert(vm.runInContext(`esc('<a href="x">&</a>')`, sandbox) === '&lt;a href="x"&gt;&amp;&lt;/a&gt;', 'esc escapes HTML metacharacters');

// --- renderDetail: lazy sections ---
const lazyEntry = {
  method: 'POST', path: '/v1/chat/completions', status_code: 200,
  timestamp_ms: 1000, duration_ms: 5,
  request_body_size: 10, response_body_size: 20,
  ai_insights: {
    request: { model: 'gpt-4o', message_count: 2, system_prompts: ['You are helpful.'] },
    response: { usage: { total_tokens: 15 } },
  },
  request_body: { model: 'gpt-4o', messages: [
    { role: 'system', content: 'You are helpful.' },
    { role: 'user', content: 'UNIQUE_USER_MESSAGE_MARKER' },
  ] },
  response_body: { choices: [{ message: { role: 'assistant', content: 'hi' } }] },
  request_headers: { 'content-type': 'application/json' },
  response_headers: { 'content-type': 'application/json' },
};
const detailHtml = vm.runInContext(
  'renderDetail(' + JSON.stringify(lazyEntry) + ')', sandbox);
assert(detailHtml.includes('data-rendered="false"'), 'sections start unrendered');
assert(detailHtml.includes('data-section="new-inputs"'), 'new inputs section present');
assert(!detailHtml.includes('data-section="messages-history"'),
  'first-turn request has no history section');
assert(detailHtml.includes('data-section="system-prompt"'), 'system prompt section present');
assert(detailHtml.includes('data-section="raw-request"'), 'raw request section present');
assert(!detailHtml.includes('UNIQUE_USER_MESSAGE_MARKER'),
  'message bodies are NOT rendered until expanded');

// --- renderDetail: turn-aware Messages split (New Inputs vs History) ---
const toolLoopEntry = {
  method: 'POST', path: '/v1/chat/completions', status_code: 200,
  timestamp_ms: 2000, duration_ms: 5,
  request_body_size: 10, response_body_size: 20,
  ai_insights: {
    request: { model: 'gpt-4o', message_count: 5 },
    response: { usage: { total_tokens: 30 } },
  },
  request_body: { model: 'gpt-4o', messages: [
    { role: 'system', content: 'You are helpful.' },
    { role: 'user', content: 'HISTORY_USER_MARKER' },
    { role: 'assistant', content: null, tool_calls: [
      { id: 'c1', type: 'function', function: { name: 'search', arguments: '{}' } },
      { id: 'c2', type: 'function', function: { name: 'read', arguments: '{}' } },
    ] },
    { role: 'tool', tool_call_id: 'c1', content: 'TOOL_RESULT_MARKER_ONE' },
    { role: 'tool', tool_call_id: 'c2', content: 'TOOL_RESULT_MARKER_TWO' },
  ] },
  response_body: { choices: [{ message: { role: 'assistant', content: 'done' } }] },
  request_headers: { 'content-type': 'application/json' },
  response_headers: { 'content-type': 'application/json' },
};
const loopHtml = vm.runInContext(
  'renderDetail(' + JSON.stringify(toolLoopEntry) + ')', sandbox);
assert(loopHtml.includes('data-section="new-inputs"'), 'tool loop: new inputs section present');
assert(loopHtml.includes('data-section="messages-history"'), 'tool loop: history section present');
assert(loopHtml.includes('New Inputs (2 &middot; tool)'),
  'tool loop: new inputs label shows count and role');
assert(loopHtml.includes('Messages History (2)'), 'tool loop: history label shows count');
assert(!loopHtml.includes('TOOL_RESULT_MARKER_ONE'),
  'tool results are NOT rendered until expanded');
const newInputsBody = vm.runInContext("sectionRenderers['new-inputs']()", sandbox);
assert(newInputsBody.includes('TOOL_RESULT_MARKER_ONE')
  && newInputsBody.includes('TOOL_RESULT_MARKER_TWO'),
  'all trailing tool results belong to new inputs');
assert(!newInputsBody.includes('HISTORY_USER_MARKER'),
  'earlier user message stays out of new inputs');
const historyBody = vm.runInContext("sectionRenderers['messages-history']()", sandbox);
assert(historyBody.includes('HISTORY_USER_MARKER'), 'history keeps the earlier user message');
assert(!historyBody.includes('TOOL_RESULT_MARKER_ONE'), 'tool results stay out of history');

// --- slim list shape: badges/filters use e.ai ---
assert(vm.runInContext(`(function() {
  var e = { ai: { model: 'gpt-4o' }, method: 'POST', path: '/x', status_code: 200 };
  return matchesFilters === undefined ? false : true;
})()`, sandbox), 'matchesFilters exists');
vm.runInContext(`filters = { method: null, path: '', status: null, source: null, tags: ['ai'] };`, sandbox);
assert(vm.runInContext(
  `matchesFilters({ ai: { model: 'x' }, method: 'POST', path: '/a', status_code: 200 })`,
  sandbox) === true, 'ai tag matches entries with slim ai object');
assert(vm.runInContext(
  `matchesFilters({ method: 'POST', path: '/a', status_code: 200 })`,
  sandbox) === false, 'ai tag does not match entries without ai');
vm.runInContext(`filters = { method: null, path: '', status: null, source: null, tags: ['tool_results'] };`, sandbox);
assert(vm.runInContext(
  `matchesFilters({ tool_results: true, method: 'POST', path: '/a', status_code: 200 })`,
  sandbox) === true, 'tool_results tag matches entries carrying tool results');
assert(vm.runInContext(
  `matchesFilters({ tools: true, method: 'POST', path: '/a', status_code: 200 })`,
  sandbox) === false, 'tool_results tag does not match tool-call-only entries');
vm.runInContext(`filters = { method: null, path: '', status: null, source: null, tags: [] };`, sandbox);

// --- pageWindow: newest-first pagination bounds ---
const win0 = vm.runInContext('pageWindow(218, 0, 200)', sandbox);
assert(win0.start === 18 && win0.end === 218, 'page 0 anchors at the newest entries');
const win1 = vm.runInContext('pageWindow(218, 1, 200)', sandbox);
assert(win1.start === 0 && win1.end === 18, 'page 1 covers the remaining older entries');
const win2 = vm.runInContext('pageWindow(218, 2, 200)', sandbox);
assert(win2.start === 0 && win2.end === 0, 'out-of-range page collapses to empty');
const winEmpty = vm.runInContext('pageWindow(0, 0, 200)', sandbox);
assert(winEmpty.start === 0 && winEmpty.end === 0, 'empty log yields empty window');
const winExact = vm.runInContext('pageWindow(200, 0, 200)', sandbox);
assert(winExact.start === 0 && winExact.end === 200, 'exact multiple fills page 0');

// --- text truncation ---
const longText = 'x'.repeat(5000);
vm.runInContext('detailTexts = []; currentDetail = {};', sandbox);
const trunc = vm.runInContext(
  'renderLongText(' + JSON.stringify(longText) + ')', sandbox);
assert(trunc.includes('x'.repeat(2000)), 'first 2000 chars rendered');
assert(!trunc.includes('x'.repeat(2001)), 'content beyond limit not rendered');
assert(trunc.includes('Show more (+3000 chars)'), 'show-more button reports remaining chars');
const shortText = vm.runInContext(`renderLongText('short')`, sandbox);
assert(shortText === 'short', 'short text renders untouched');

// --- json tree child cap ---
vm.runInContext('detailValues = [];', sandbox);
const bigArr = vm.runInContext(
  'renderJsonTree({ arr: Array.from({length: 250}, function(_, i) { return i; }) })', sandbox);
assert(bigArr.includes('150 more (load)'), 'children beyond 100 are capped with load row');
assert(bigArr.includes('data-val-idx'), 'load row references stored value');

// --- copy source cap ---
const hugeObj = 'renderJsonTree({ big: ' + JSON.stringify('y'.repeat(150000)) + ' })';
const hugeTree = vm.runInContext(hugeObj, sandbox);
assert(!hugeTree.includes('json-copy-src'), 'huge trees skip the hidden copy source');
const smallTree = vm.runInContext(`renderJsonTree({ a: 1 })`, sandbox);
assert(smallTree.includes('json-copy-src'), 'small trees keep the hidden copy source');

// --- msgContent truncation ---
const msgHtml = vm.runInContext(
  'msgContent(' + JSON.stringify('z'.repeat(4000)) + ')', sandbox);
assert(msgHtml.includes('Show more'), 'message content is truncated');
assert(!msgHtml.includes('z'.repeat(2001)), 'message content capped at limit');

if (failures > 0) {
  console.error(failures + ' assertion(s) failed');
  process.exit(1);
}
console.log('SMOKE OK');
