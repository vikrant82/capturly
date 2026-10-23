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

// --- Bedrock Converse rendering uses native messages and the combined stream response ---
const bedrockEntry = {
  method: 'POST', path: '/anthropic/deployments/dpl-4/converse-stream', status_code: 200,
  timestamp_ms: 3000, duration_ms: 8, request_body_size: 40, response_body_size: 12,
  eventstream: true, eventstream_status: 'completed', response_body_encoding: 'base64',
  response_body: 'AQIDBASE64',
  ai_insights: {
    protocol: 'bedrock_converse',
    request: {
      model: 'gateway-bedrock-model', message_count: 3, roles: ['user', 'assistant', 'user'],
      system_prompts: ['Use the tool safely'], stream: true, has_tool_results: true,
      tool_names: ['search'], tool_count: 1,
    },
    response: {
      model: 'gateway-bedrock-model',
      usage: { inputTokens: 12, outputTokens: 5, totalTokens: 17, cacheReadInputTokens: 3,
        prompt_tokens: 12, completion_tokens: 5, total_tokens: 17 },
      finish_reasons: ['tool_use'], assistant_content: ['<script>alert(1)</script>'],
    },
  },
  request_body: {
    modelId: 'gateway-bedrock-model', system: [{ text: 'Use the tool safely' }],
    toolConfig: { tools: [{ toolSpec: {
      name: 'search', description: 'Find items',
      inputSchema: { json: { type: 'object', properties: { q: { type: 'string' } } } },
    } }] },
    messages: [
      { role: 'user', content: [{ text: 'OLD_HISTORY_MARKER' }] },
      { role: 'assistant', content: [{ text: 'prior answer' }] },
      { role: 'user', content: [{ toolResult: { toolUseId: 'tool-1', content: [
        { text: '<img src=x onerror=alert(2)>' }, { json: { found: true } },
      ] } }] },
    ],
  },
  eventstream_response: {
    output: { message: { role: 'assistant', content: [
      { text: '<script>alert(1)</script>' },
      { toolUse: { toolUseId: 'tool-2', name: 'search', input: { q: 'cats' } } },
      { reasoningContent: { reasoningText: { text: 'private rationale', signature: 'sig-42' } } },
      { image: { format: 'png', source: { bytes: 'iVBORw0KGgo=' } } },
    ] } },
    stopReason: 'tool_use', usage: { inputTokens: 12, outputTokens: 5, totalTokens: 17 },
  },
  request_headers: {}, response_headers: {},
};
const bedrockDetail = vm.runInContext(
  'renderDetail(' + JSON.stringify(bedrockEntry) + ')', sandbox);
assert(bedrockDetail.includes('Bedrock Converse'), 'Bedrock protocol is visible');
assert(bedrockDetail.includes('gateway-bedrock-model'), 'Bedrock request model is visible');
assert(bedrockDetail.includes('Stream') && bedrockDetail.includes('Yes'), 'Bedrock stream state is visible');
assert(bedrockDetail.includes('12 prompt + 5 completion = 17 total'), 'Bedrock usage is visible');
assert(bedrockDetail.includes('Finish') && bedrockDetail.includes('tool_use'), 'Bedrock stop reason is visible');
assert(bedrockDetail.includes('Tools (1)'), 'Bedrock tool definitions are visible');
assert(bedrockDetail.includes('New Inputs (1 &middot; user)'), 'user tool results remain user messages');
assert(bedrockDetail.includes('Messages History (2)'), 'Bedrock message history is split by assistant turns');
assert(bedrockDetail.includes('EventStream Info'), 'EventStream status section is visible');
const bedrockTools = vm.runInContext("sectionRenderers['tools']()", sandbox);
assert(bedrockTools.includes('search') && bedrockTools.includes('Input schema'), 'Bedrock tool schema renders');
const bedrockInputs = vm.runInContext("sectionRenderers['new-inputs']()", sandbox);
assert(bedrockInputs.includes('Tool result') && bedrockInputs.includes('tool-1'), 'user tool result renders structurally');
assert(bedrockInputs.includes('&lt;img src=x onerror=alert(2)&gt;'), 'untrusted tool result text is escaped');
const bedrockAssistant = vm.runInContext("sectionRenderers['assistant-response']()", sandbox);
assert(bedrockAssistant.includes('search') && bedrockAssistant.includes('cats'), 'native tool use renders');
assert(bedrockAssistant.includes('reasoningContent') && bedrockAssistant.includes('sig-42'),
  'reasoning and signature remain structurally inspectable');
assert(bedrockAssistant.includes('image') && bedrockAssistant.includes('iVBORw0KGgo='),
  'multimodal blocks remain structurally inspectable');
assert(bedrockAssistant.includes('&lt;script&gt;alert(1)&lt;/script&gt;')
  && !bedrockAssistant.includes('<script>alert(1)</script>'), 'native response text is safely escaped');
const rawBedrockResponse = vm.runInContext("sectionRenderers['raw-response']()", sandbox);
assert(rawBedrockResponse.includes('AQIDBASE64') && !rawBedrockResponse.includes('eventstream_response'),
  'raw EventStream bytes remain inspectable as encoded data, not parsed JSON');

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

// --- Rich AI Meta token stats & Context Window ---
assert(vm.runInContext(`defaultContextWindow('claude-3-7-sonnet')`, sandbox) === 200000,
  'claude models default to 200k context window');
assert(vm.runInContext(`defaultContextWindow('gemini-2.5-pro')`, sandbox) === 1000000,
  'gemini models default to 1M context window');
assert(vm.runInContext(`defaultContextWindow('gpt-4.1-mini')`, sandbox) === 400000,
  'gpt-4.1 models default to 400k context window');
assert(vm.runInContext(`defaultContextWindow('gpt-4-0613')`, sandbox) === 32000,
  'legacy gpt-4 defaults to 32k context window');
assert(vm.runInContext(`defaultContextWindow('gpt-4o')`, sandbox) === 128000,
  'gpt-4o defaults to 128k context window');

// Bedrock Converse detail from above includes Cached Read (3 / (12 + 3) = 20%) and Context Window (12 + 3 + 5 = 20 used)
assert(bedrockDetail.includes('Cached Read') && bedrockDetail.includes('3 (20%)'),
  'Bedrock Cached Read token count and percentage of total input are visible');
assert(bedrockDetail.includes('20 / 128,000 (0.02%)'),
  'Bedrock context window includes cached tokens in used count');

const richOpenAIEntry = {
  method: 'POST', path: '/v1/chat/completions', status_code: 200,
  timestamp_ms: 5000, duration_ms: 12,
  request_body_size: 50, response_body_size: 80,
  ai_insights: {
    request: { model: 'gpt-4o', message_count: 1, max_tokens: 4096, stream: true },
    response: {
      model: 'gpt-4o',
      usage: {
        prompt_tokens: 1000,
        completion_tokens: 500,
        total_tokens: 1500,
        cache_read_tokens: 500,
        cache_write_tokens: 100,
        thinking_tokens: 125,
      },
      finish_reasons: ['stop'],
    },
  },
  request_body: { model: 'gpt-4o', max_completion_tokens: 4096, messages: [{ role: 'user', content: 'hi' }] },
  response_body: { choices: [{ message: { role: 'assistant', content: 'hello' } }] },
  request_headers: {}, response_headers: {},
};
const richMetaHtml = vm.runInContext('renderAIMeta(' + JSON.stringify(richOpenAIEntry) + ')', sandbox);
assert(richMetaHtml.includes('<span class="meta-key">Input:</span> <span class="meta-val">1,000</span>'),
  'Input tokens rendered');
assert(richMetaHtml.includes('<span class="meta-key">Cached Read:</span> <span class="meta-val">500 (50%)</span>'),
  'Cached Read tokens with percentage rendered');
assert(richMetaHtml.includes('<span class="meta-key">Cache Write:</span> <span class="meta-val">100</span>'),
  'Cache Write tokens rendered');
assert(richMetaHtml.includes('<span class="meta-key">Thinking:</span> <span class="meta-val">125 (25%)</span>'),
  'Thinking tokens with percentage rendered');
assert(richMetaHtml.includes('<span class="meta-key">Output:</span> <span class="meta-val">500</span>'),
  'Output tokens rendered');
assert(richMetaHtml.includes('<span class="meta-key">Max Output:</span> <span class="meta-val">4,096</span>'),
  'Max Output tokens rendered');
assert(richMetaHtml.includes('1,500 / 128,000 (1.2%)'),
  'Context Window usage text rendered with 128k default for gpt-4o');
assert(richMetaHtml.includes('id="ctx-window-select"') && richMetaHtml.includes('id="ctx-window-custom"'),
  'Context Window interactive preset select and custom input rendered');

// Interactive context window updates and localStorage persistence per model
vm.runInContext(`
  var _store = {};
  localStorage = {
    getItem: function(k) { return Object.prototype.hasOwnProperty.call(_store, k) ? _store[k] : null; },
    setItem: function(k, v) { _store[k] = String(v); },
    removeItem: function(k) { delete _store[k]; },
  };
  var _els = {
    'ctx-window-usage': {
      textContent: '',
      attrs: { 'data-used-tokens': '1500', 'data-model-key': 'gpt-4o', 'data-default-limit': '128000' },
      getAttribute: function(name) { return this.attrs[name]; },
    },
    'ctx-window-bar': { style: { width: '', background: '' } },
    'ctx-window-select': {
      value: '200000',
      getAttribute: function(name) { return name === 'data-model-key' ? 'gpt-4o' : null; },
    },
    'ctx-window-custom': {
      value: '128000',
      getAttribute: function(name) { return name === 'data-model-key' ? 'gpt-4o' : null; },
    },
  };
  document.getElementById = function(id) { return _els[id] || null; };
  onContextWindowSelectChange(_els['ctx-window-select']);
`, sandbox);
assert(vm.runInContext(`_els['ctx-window-usage'].textContent`, sandbox) === '1,500 / 200,000 (0.75%)',
  'Selecting 200k preset updates Context Window usage text in DOM');
assert(vm.runInContext(`_els['ctx-window-custom'].value`, sandbox) === '200000',
  'Selecting 200k preset syncs custom input value');
assert(vm.runInContext(`localStorage.getItem('capturly_ctx_window:gpt-4o')`, sandbox) === '200000',
  'Selecting preset persists context window for model in localStorage');

vm.runInContext(`
  _els['ctx-window-custom'].value = '50000';
  onContextWindowCustomInput(_els['ctx-window-custom']);
`, sandbox);
assert(vm.runInContext(`_els['ctx-window-usage'].textContent`, sandbox) === '1,500 / 50,000 (3%)',
  'Custom context window input updates usage text in DOM');
assert(vm.runInContext(`_els['ctx-window-select'].value`, sandbox) === 'custom',
  'Non-preset custom value switches select to custom option');
assert(vm.runInContext(`getContextWindowConfig('gpt-4o').limit`, sandbox) === 50000,
  'getContextWindowConfig reads persisted custom limit for gpt-4o');
assert(vm.runInContext(`getContextWindowConfig('claude-3-7-sonnet').limit`, sandbox) === 200000,
  'Other models keep their own default context window');

// --- HTML attribute escaping for data-model-key ---
const quoteModelHtml = vm.runInContext(
  `renderContextWindowMetaItem('gpt-4o" onclick="alert(1)', 500)`, sandbox);
assert(quoteModelHtml.includes('data-model-key="gpt-4o&quot; onclick=&quot;alert(1)"'),
  'double quotes in model name are escaped as &quot; in data-model-key attributes');
assert(!quoteModelHtml.includes('data-model-key="gpt-4o" onclick="alert(1)"'),
  'unquoted attribute injection is prevented in data-model-key');

// --- Empty usage: {} handling & fallback to response_body.usage ---
const emptyUsageEntry = {
  method: 'POST', path: '/model/claude-3-7-sonnet/converse', status_code: 200,
  ai_insights: {
    protocol: 'bedrock_converse',
    request: { model: 'claude-3-7-sonnet', max_tokens: 2048 },
    response: { model: 'claude-3-7-sonnet', usage: {}, finish_reasons: ['end_turn'] },
  },
  request_body: { modelId: 'claude-3-7-sonnet' },
  response_body: { output: { message: { role: 'assistant', content: [{ text: 'hi' }] } } },
};
const emptyUsageHtml = vm.runInContext(
  'renderAIMeta(' + JSON.stringify(emptyUsageEntry) + ')', sandbox);
assert(!emptyUsageHtml.includes('Tokens:') && !emptyUsageHtml.includes('Context Window:'),
  'empty usage: {} without response_body usage omits fabricated zero token stats');
assert(emptyUsageHtml.includes('<span class="meta-key">Max Output:</span> <span class="meta-val">2,048</span>'),
  'Max Output still renders when usage is empty');

const fallbackUsageEntry = {
  method: 'POST', path: '/model/claude-3-7-sonnet/converse', status_code: 200,
  ai_insights: {
    protocol: 'bedrock_converse',
    request: { model: 'claude-3-7-sonnet' },
    response: { model: 'claude-3-7-sonnet', usage: {}, finish_reasons: ['end_turn'] },
  },
  request_body: { modelId: 'claude-3-7-sonnet' },
  response_body: {
    output: { message: { role: 'assistant', content: [{ text: 'hi' }] } },
    usage: { inputTokens: 80, outputTokens: 20, totalTokens: 100 },
  },
};
const fallbackUsageHtml = vm.runInContext(
  'renderAIMeta(' + JSON.stringify(fallbackUsageEntry) + ')', sandbox);
assert(fallbackUsageHtml.includes('80 prompt + 20 completion = 100 total'),
  'empty ai_insights.response.usage falls back to valid response_body.usage');

// --- OpenAI Responses API rendering ---
const responsesEntry = {
  method: 'POST', path: '/awsgpt/deployments/gpt-5-6-luna/v1/responses', status_code: 200,
  timestamp_ms: 9000, duration_ms: 42, request_body_size: 120, response_body_size: 240,
  sse: true, sse_combine_status: 'completed', sse_duration_ms: 40,
  ai_insights: {
    protocol: 'openai_responses',
    request: {
      model: 'gpt-5-6-luna',
      message_count: 4,
      roles: ['developer', 'user', 'assistant', 'tool'],
      system_prompts: ['You are a helpful coding agent.'],
      stream: true,
      has_tool_results: true,
      tool_names: ['list_dir'],
      tool_count: 1,
    },
    response: {
      model: 'gpt-5.4-2026-03-05',
      usage: {
        input_tokens: 11521,
        input_tokens_details: { cached_tokens: 1408 },
        output_tokens: 413,
        output_tokens_details: { reasoning_tokens: 344 },
        total_tokens: 11934,
        prompt_tokens: 11521,
        completion_tokens: 413,
        cache_read_tokens: 1408,
        thinking_tokens: 344,
      },
      finish_reasons: ['completed', 'tool_calls'],
      assistant_content: ['Here are the files in the directory.'],
      tool_call_names: ['list_dir'],
      tool_call_count: 1,
    },
  },
  request_body: {
    model: 'gpt-5-6-luna',
    max_output_tokens: 16384,
    stream: true,
    input: [
      { role: 'developer', content: [{ type: 'input_text', text: 'You are a helpful coding agent.' }] },
      { role: 'user', content: [{ type: 'input_text', text: 'RESPONSES_HISTORY_USER_MARKER' }] },
      { type: 'function_call', id: 'fc_1', call_id: 'call_101', name: 'list_dir', arguments: '{"path":"."}' },
      { type: 'function_call_output', call_id: 'call_101', output: 'RESPONSES_TOOL_OUTPUT_MARKER' },
    ],
    tools: [
      {
        type: 'function',
        name: 'list_dir',
        description: 'List directory contents',
        parameters: { type: 'object', properties: { path: { type: 'string' } } },
      },
    ],
  },
  response_body: {
    id: 'resp_01',
    object: 'response',
    model: 'gpt-5.4-2026-03-05',
    status: 'completed',
    output: [
      {
        id: 'rs_01',
        type: 'reasoning',
        encrypted_content: 'gAAAAAB',
        summary: [{ type: 'summary_text', text: 'RESPONSES_REASONING_SUMMARY_MARKER' }],
      },
      {
        id: 'msg_01',
        type: 'message',
        role: 'assistant',
        status: 'completed',
        content: [{ type: 'output_text', text: 'Here are the files in the directory.', annotations: [] }],
      },
      {
        id: 'fc_02',
        type: 'function_call',
        call_id: 'call_202',
        name: 'read_file',
        arguments: '{"path":"README.md"}',
      },
    ],
    usage: {
      input_tokens: 11521,
      input_tokens_details: { cached_tokens: 1408 },
      output_tokens: 413,
      output_tokens_details: { reasoning_tokens: 344 },
      total_tokens: 11934,
    },
  },
  request_headers: {}, response_headers: {},
};
const responsesDetail = vm.runInContext(
  'renderDetail(' + JSON.stringify(responsesEntry) + ')', sandbox);
assert(responsesDetail.includes('OpenAI Responses'), 'OpenAI Responses protocol badge is visible');
assert(responsesDetail.includes('gpt-5-6-luna'), 'OpenAI Responses model is visible');
assert(responsesDetail.includes('11521 prompt + 413 completion = 11934 total'), 'OpenAI Responses token summary is visible');
assert(responsesDetail.includes('<span class="meta-key">Max Output:</span> <span class="meta-val">16,384</span>'),
  'OpenAI Responses max_output_tokens renders as Max Output');
assert(responsesDetail.includes('11,934 / 400,000 (3%)'),
  'gpt-5-6-luna defaults to 400k context window');
assert(responsesDetail.includes('New Inputs (1 &middot; tool)'),
  'Responses API function_call_output after function_call is treated as new tool input');
assert(responsesDetail.includes('Messages History (2)'),
  'Responses API user message and function_call are placed in history');
assert(responsesDetail.includes('data-section="assistant-response"'),
  'Responses API output array registers assistant-response section');

const responsesNewInputs = vm.runInContext("sectionRenderers['new-inputs']()", sandbox);
assert(responsesNewInputs.includes('RESPONSES_TOOL_OUTPUT_MARKER') && responsesNewInputs.includes('call_101'),
  'Responses API function_call_output renders with call_id and output text');
const responsesHistory = vm.runInContext("sectionRenderers['messages-history']()", sandbox);
assert(responsesHistory.includes('RESPONSES_HISTORY_USER_MARKER') && responsesHistory.includes('list_dir'),
  'Responses API history renders input_text cleanly and function_call as tool card');
assert(!responsesHistory.includes('"type": "input_text"'),
  'Responses API input_text parts render as plain text, not raw JSON');
const responsesAssistant = vm.runInContext("sectionRenderers['assistant-response']()", sandbox);
assert(responsesAssistant.includes('RESPONSES_REASONING_SUMMARY_MARKER'),
  'Responses API reasoning summary renders in assistant response');
assert(responsesAssistant.includes('Here are the files in the directory.'),
  'Responses API message output_text renders in assistant response');
assert(responsesAssistant.includes('read_file') && responsesAssistant.includes('call_202'),
  'Responses API function_call output item renders in assistant response');

if (failures > 0) {
  console.error(failures + ' assertion(s) failed');
  process.exit(1);
}
console.log('SMOKE OK');
