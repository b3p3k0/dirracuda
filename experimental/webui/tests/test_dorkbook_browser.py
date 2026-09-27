"""Execute browser default reconciliation with QuickJS, without live services."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_browser_default_refresh_preserves_manual_input_and_explicit_apply(tmp_path):
    qjs = shutil.which("qjs")
    if not qjs:
        pytest.skip("QuickJS is unavailable")
    harness = tmp_path / "defaults.mjs"
    script_path = json.dumps(str(STATIC / "dorkbook_defaults.js"))
    harness.write_text('''
import * as std from 'std';
function assert(value, label) { if (!value) throw new Error(label); }
const field = {value: '', dataset: {dorkDestination: 'self_hosted'}};
const events = {};
let onApply;
globalThis.BroadcastChannel = class {
  constructor(name) { assert(name === 'dirracuda.dorkbook.applied', 'channel name'); }
  addEventListener(name, callback) { assert(name === 'message', 'message subscription'); onApply = callback; }
};
let pending = [];
globalThis.document = {querySelectorAll: () => [field]};
globalThis.window = {addEventListener: (name, callback) => {events[name] = callback;}};
globalThis.fetch = () => new Promise(resolve => pending.push(resolve));
async function reply(query) {
  pending.shift()({ok: true, json: async () => ({defaults: {self_hosted: query}})});
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
}
std.loadScript(SCRIPT_PATH);
await reply('saved one');
assert(field.value === 'saved one', 'initial saved default');
field.value = 'manual';
events.focus(); await reply('saved two');
assert(field.value === 'manual', 'focus preserves manual input');
field.value = 'saved two';
events.focus(); await reply('saved three');
assert(field.value === 'saved three', 'clean field follows latest default');
events.focus();
onApply({data: {destination: 'self_hosted', query: 'explicit apply'}});
await reply('stale default');
assert(field.value === 'explicit apply', 'late response cannot overwrite explicit apply');
onApply({data: {destination: 'shodan:HTTP', query: 'other provider'}});
assert(field.value === 'explicit apply', 'unrelated destination untouched');
onApply({data: null});
assert(field.value === 'explicit apply', 'malformed event ignored');
'''.replace('SCRIPT_PATH', script_path), encoding="utf-8")
    result = subprocess.run([qjs, str(harness)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("filename", ["dorkbook.js", "dorkbook_defaults.js"])
def test_browser_javascript_compiles(filename, tmp_path):
    compiler = shutil.which("qjsc")
    if not compiler:
        pytest.skip("QuickJS compiler is unavailable")
    result = subprocess.run([compiler, "-c", "-o", str(tmp_path / "out.c"), str(STATIC / filename)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("broadcast_available", [True, False])
def test_library_selection_only_previews_and_copy_has_http_fallback(tmp_path, broadcast_available):
    qjs = shutil.which("qjs")
    if not qjs:
        pytest.skip("QuickJS is unavailable")
    harness = tmp_path / "library.mjs"
    script_path = json.dumps(str(STATIC / "dorkbook.js"))
    harness.write_text('''
import * as std from 'std';
function assert(value, label) { if (!value) throw new Error(label); }
const elements = {}, requests = [], broadcasts = [];
let persistedQueries = 0;
globalThis.localStorage = {setItem() { persistedQueries += 1; }};
globalThis.BroadcastChannel = class {
  constructor() { if (!BROADCAST_SUPPORT) throw new Error('Unsupported'); }
  postMessage(data) { broadcasts.push(data); }
};
class Element {
  constructor() { this.value = ''; this.children = []; this.dataset = {}; this.events = {}; }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  add(child) { this.children.push(child); }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
}
function element(id) { return elements[id] || (elements[id] = new Element()); }
const selection = {removeAllRanges() {}, addRange(range) { this.range = range; }};
globalThis.document = {
  getElementById: element,
  createElement: () => new Element(),
  createRange: () => ({selectNodeContents(node) { this.node = node; }}),
  querySelector: () => ({content: 'csrf'}),
  querySelectorAll: () => element('dorkbook-tbody').children.filter(row => row.dataset.entryId)
};
globalThis.window = {location: {search: ''}, addEventListener() {}, getSelection: () => selection};
globalThis.navigator = {};
globalThis.URLSearchParams = class { get() { return null; } };
globalThis.Option = class extends Element {};
const row = {entry_id: 1, nickname: 'Books', provider: 'self_hosted', protocol: null,
             topic: 'Books', query: 'intitle:"Index of /" epub', notes: 'Test notes', row_kind: 'builtin'};
globalThis.fetch = async (url, options) => {
  requests.push({url, options});
  return {ok: true, json: async () => url.endsWith('entries') ? {entries: [row]} :
    url.endsWith('apply') ? {provider: 'self_hosted', protocol: null, destination: 'self_hosted', query: row.query} :
    {defaults: {self_hosted: row.query}}};
};
std.loadScript(SCRIPT_PATH);
for (let i = 0; i < 12; i++) await Promise.resolve();
const rendered = element('dorkbook-tbody').children.find(item => item.dataset.entryId === 1);
assert(rendered, 'library row rendered');
rendered.events.click();
assert(element('preview-query').textContent === row.query, 'selection previews full query');
assert(element('preview-kind').textContent === 'Built-in · read-only', 'built-in status');
assert(element('delete-dork').disabled, 'built-in deletion disabled');
assert(requests.every(request => !request.options.method), 'selection never writes or applies');
await element('copy-dork').events.click();
assert(selection.range.node === element('preview-query'), 'HTTP fallback selects query');
assert(element('dorkbook-status').textContent.includes('Ctrl+C'), 'fallback gives exact copy steps');
let copied;
navigator.clipboard = {writeText: async query => {copied = query;}};
await element('copy-dork').events.click();
assert(copied === row.query, 'clipboard gets exact query');
assert(element('dorkbook-status').textContent === 'Query copied.', 'copy success status');
await element('apply-dork').events.click();
assert(persistedQueries === 0, 'query never persisted in browser storage');
assert(broadcasts.length === (BROADCAST_SUPPORT ? 1 : 0), 'ephemeral notification when supported');
if (BROADCAST_SUPPORT) assert(broadcasts[0].query === row.query, 'exact query broadcast');
'''.replace('SCRIPT_PATH', script_path).replace('BROADCAST_SUPPORT', json.dumps(broadcast_available)), encoding="utf-8")
    result = subprocess.run([qjs, str(harness)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
