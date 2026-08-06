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
assert(tree.includes('json-copy-src'), 'hidden copy source present');
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

if (failures > 0) {
  console.error(failures + ' assertion(s) failed');
  process.exit(1);
}
console.log('SMOKE OK');
