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
assert(detailHtml.includes('data-section="messages"'), 'messages section present');
assert(detailHtml.includes('data-section="system-prompt"'), 'system prompt section present');
assert(detailHtml.includes('data-section="raw-request"'), 'raw request section present');
assert(!detailHtml.includes('UNIQUE_USER_MESSAGE_MARKER'),
  'message bodies are NOT rendered until expanded');

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
vm.runInContext(`filters = { method: null, path: '', status: null, source: null, tags: [] };`, sandbox);

if (failures > 0) {
  console.error(failures + ' assertion(s) failed');
  process.exit(1);
}
console.log('SMOKE OK');
