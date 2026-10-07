const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function adapter({pendingStream = false} = {}) {
  const requests = [];
  let streamHandlers;
  let unsubscribed;
  let resolveStream;
  const bridge = {
    ready: async () => ({isDark: false}),
    onContext: () => () => {},
    apiPost: async (endpoint, body) => {requests.push({endpoint, body}); return {ok: true, count: 2};},
    subscribeSSE: async (endpoint, handlers, params) => {
      streamHandlers = handlers;
      requests.push({endpoint, params});
      return pendingStream ? new Promise(resolve => {resolveStream = resolve;}) : 'sse-1';
    },
    unsubscribeSSE: async id => {unsubscribed = id;},
    download: async (endpoint, params, filename) => {requests.push({endpoint, params, filename});},
  };
  const window = {AstrBotPluginPage: bridge, addEventListener() {}};
  const context = {window, document: {addEventListener() {}, documentElement: {dataset: {}}, querySelectorAll: () => []},
    URL, URLSearchParams, setTimeout, clearTimeout, console};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../pages/control/console-adapter.js'), 'utf8'), context);
  return {panel: window.OoptraPanel, requests, failBoot: () => {window.__ooptraBootFailed = true;}, handlers: () => streamHandlers,
    resolveStream: () => resolveStream('sse-1'), unsubscribed: () => unsubscribed};
}

test('full console calls bridge with parsed params, never direct fetch or browser token', async () => {
  const a = adapter();
  const result = await a.panel.api('/api/voice/members?area=AREA-A&channel=CHANNEL-A');
  assert.equal(result.count, 2);
  assert.equal(a.requests[0].endpoint, 'ui/console');
  assert.equal(a.requests[0].body.path, '/api/voice/members');
  assert.equal(a.requests[0].body.params.area, 'AREA-A');
  assert.equal(a.requests[0].body.params.channel, 'CHANNEL-A');
});

test('DELETE memory and POST config preserve methods through bridge', async () => {
  const a = adapter();
  await a.panel.api('/api/memory', {method: 'DELETE', body: {}});
  await a.panel.api('/api/config', {method: 'POST', body: {updates: {voice: {backend: 'mimo_cascade'}}}});
  assert.equal(a.requests[0].body.method, 'DELETE');
  assert.equal(a.requests[1].body.body.updates.voice.backend, 'mimo_cascade');
});

test('full console preferences work without sandbox LocalStorage', () => {
  const {panel} = adapter();
  assert.equal(panel.storage.getItem('area'), null);
  panel.storage.setItem('area', 'AREA-A');
  assert.equal(panel.storage.getItem('area'), 'AREA-A');
  panel.storage.removeItem('area');
  assert.equal(panel.storage.getItem('area'), null);
});

test('console logout is per-page and login resumes the backend credential bridge', async () => {
  const a = adapter();
  await a.panel.api('/api/auth/logout', {method: 'POST'});
  assert.equal(a.panel.storage.getItem('oopz.webui.signedout'), '1');
  assert.equal((await a.panel.api('/api/auth/status')).authenticated, false);
  await assert.rejects(a.panel.api('/api/status'), /请先登录/);
  assert.throws(() => new a.panel.EventSource('/api/logs/stream'), /请先登录/);
  await assert.rejects(a.panel.downloadBackup('a'.repeat(32)), /请先登录/);
  await a.panel.api('/api/auth/login', {method: 'POST', body: {password: 'test-password'}});
  assert.equal(a.panel.storage.getItem('oopz.webui.signedout'), null);
  assert.equal((await a.panel.api('/api/status')).ok, true);
});

test('failed startup stops API, SSE and authenticated downloads', async () => {
  const a = adapter();
  a.failBoot();
  await assert.rejects(a.panel.api('/api/status'), /初始化失败/);
  assert.throws(() => new a.panel.EventSource('/api/logs/stream'), /初始化失败/);
  await assert.rejects(a.panel.downloadBackup('a'.repeat(32)), /初始化失败/);
  await assert.rejects(a.panel.openLink('/api/logs/tail'), /初始化失败/);
  assert.deepEqual(a.requests, []);
});

test('stopping an initialized console releases its SSE subscription', async () => {
  const a = adapter();
  new a.panel.EventSource('/api/logs/stream');
  await new Promise(resolve => setTimeout(resolve, 0));
  a.panel.stop();
  assert.equal(a.unsubscribed(), 'sse-1');
});

test('named SSE events reach original console listeners and unsubscribe', async () => {
  const a = adapter();
  const stream = new a.panel.EventSource('/api/logs/stream?file=oopz_bot.log&lines=10');
  let line;
  stream.addEventListener('line', event => {line = JSON.parse(event.data).line;});
  await new Promise(resolve => setTimeout(resolve, 0));
  a.handlers().onMessage({eventType: 'line', raw: '{"line":"room joined"}'});
  assert.equal(line, 'room joined');
  stream.close();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(a.unsubscribed(), 'sse-1');
});

test('SSE closed before subscribe resolves still releases subscription', async () => {
  const a = adapter({pendingStream: true});
  const stream = new a.panel.EventSource('/api/logs/stream?lines=5');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(a.requests[0].endpoint, 'ui/logs');
  stream.close();
  a.resolveStream();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(a.unsubscribed(), 'sse-1');
});

test('log download uses Dashboard bridge rather than sandbox popup', async () => {
  const a = adapter();
  await a.panel.openLink('/api/logs/tail?lines=20000&file=oopz_bot.log');
  assert.equal(a.requests[0].endpoint, 'ui/logs-download');
  assert.equal(a.requests[0].params.lines, '20000');
});

test('backup download stays in the authenticated bridge with a fixed identifier', async () => {
  const a = adapter();
  await a.panel.downloadBackup('a'.repeat(32));
  assert.equal(a.requests[0].endpoint, 'ui/backup-download');
  assert.equal(a.requests[0].filename, 'ooptra-' + 'a'.repeat(32) + '.zip');
  await assert.rejects(a.panel.downloadBackup('../config.py'));
  assert.equal(a.requests.length, 1);
});
