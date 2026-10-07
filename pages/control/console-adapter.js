/* Adapt Ooptra's existing console to AstrBot's authenticated, sandboxed view. */
(() => {
  'use strict';
  const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
  const values = new Map();
  const logSources = new Set();
  function ensureRunning() {
    if (window.__ooptraBootFailed) throw new Error('部署中的控制台初始化失败，请刷新重试。');
  }
  const storage = {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  };
  const ready = bridge ? bridge.ready() : Promise.reject(new Error('请从 AstrBot 插件详情打开控制台。'));
  const parse = path => {
    const url = new URL(path, 'https://ooptra.invalid');
    return {path: url.pathname, params: Object.fromEntries(url.searchParams)};
  };

  async function api(path, opts = {}) {
    await ready;
    ensureRunning();
    const parsed = parse(path);
    // Ooptra's token is attached by the plugin backend, never by the browser.
    delete parsed.params.token;
    const authPath = parsed.path.startsWith('/api/auth/');
    if (!authPath) ensureSignedIn();
    const result = await bridge.apiPost('ui/console', {...parsed, method: opts.method || 'GET', body: opts.body || {}});
    if (parsed.path === '/api/auth/status' && values.get('oopz.webui.signedout') === '1') {
      return {...result, authenticated: false};
    }
    if (parsed.path === '/api/auth/logout') values.set('oopz.webui.signedout', '1');
    if (['/api/auth/login', '/api/auth/setup'].includes(parsed.path)) values.delete('oopz.webui.signedout');
    return result;
  }

  function ensureSignedIn() {
    if (values.get('oopz.webui.signedout') === '1') throw new Error('请先登录完整控制台。');
  }

  class LogSource {
    constructor(path) {
      ensureRunning();
      ensureSignedIn();
      this.listeners = new Map();
      this.closed = false;
      this.subscription = null;
      this.retryTimer = null;
      this.params = parse(path).params;
      logSources.add(this);
      this.connect();
    }
    async connect() {
      try {
        await ready;
        if (this.closed) return;
        ensureRunning();
        ensureSignedIn();
        const id = await bridge.subscribeSSE('ui/logs', {
          onMessage: event => {
            if (this.closed) return;
            if (event.eventType === 'error') return this.failed();
            const message = {data: event.raw, type: event.eventType};
            (this.listeners.get(event.eventType) || []).forEach(handler => handler(message));
          },
          onError: () => this.failed(),
        }, this.params);
        if (this.closed) await bridge.unsubscribeSSE(id);
        else this.subscription = id;
      } catch (_) { this.failed(); }
    }
    failed() {
      if (this.closed || this.retryTimer) return;
      if (this.onerror) this.onerror();
      this.retryTimer = setTimeout(async () => {
        this.retryTimer = null;
        if (this.subscription) await bridge.unsubscribeSSE(this.subscription).catch(() => {});
        this.subscription = null;
        if (!this.closed) this.connect();
      }, 3000);
    }
    addEventListener(name, handler) {
      const handlers = this.listeners.get(name) || [];
      handlers.push(handler);
      this.listeners.set(name, handlers);
    }
    close() {
      logSources.delete(this);
      this.closed = true;
      clearTimeout(this.retryTimer);
      if (this.subscription) bridge.unsubscribeSSE(this.subscription).catch(() => {});
    }
  }

  async function openLink(path) {
    if (path.startsWith('/api/logs/tail')) {
      await ready;
      ensureRunning();
      ensureSignedIn();
      return bridge.download('ui/logs-download', parse(path).params, 'ooptra-logs.txt');
    }
    // External project links have no navigation privilege in the view sandbox.
    const url = new URL(path);
    if (!['http:', 'https:'].includes(url.protocol)) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'console-link-dialog';
    const title = document.createElement('h3');
    title.textContent = '外部链接';
    const input = document.createElement('input');
    input.className = 'control';
    input.value = url.href;
    input.readOnly = true;
    input.setAttribute('aria-label', '外部链接地址');
    const note = document.createElement('p');
    note.className = 'hint';
    note.textContent = '复制这个地址，在浏览器中打开。';
    const close = document.createElement('button');
    close.className = 'btn primary';
    close.textContent = '关闭';
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => dialog.remove());
    dialog.append(title, input, note, close);
    document.body.append(dialog);
    dialog.showModal();
    input.focus();
    input.select();
  }

  async function downloadBackup(id) {
    if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('备份 ID 无效。');
    await ready;
    ensureRunning();
    ensureSignedIn();
    return bridge.download('ui/backup-download', {id}, `ooptra-${id}.zip`);
  }

  window.OoptraPanel = {api, storage, EventSource: LogSource, openLink, downloadBackup,
    stop: () => {window.__ooptraBootFailed = true; [...logSources].forEach(source => source.close());}};
  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('console-load-title')) return;
    if (window.__ooptraAdapterMounted) return;
    window.__ooptraAdapterMounted = true;
    document.addEventListener('click', event => {
      const anchor = event.target.closest?.('a[href]');
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      const url = new URL(href, 'https://ooptra.invalid');
      if (url.pathname.startsWith('/api/maintenance/backups/')) {
        event.preventDefault();
        downloadBackup(url.pathname.split('/').pop()).catch(error => {
          if (typeof window.toast === 'function') window.toast('下载失败', error.message, 'err');
        });
      } else if (/^https?:\/\//i.test(href)) {
        event.preventDefault();
        openLink(href);
      }
    });
    const buttons = document.querySelectorAll('[data-theme-toggle]');
    function apply(dark) {
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      buttons.forEach(button => button.setAttribute('aria-checked', String(dark)));
    }
    if (bridge) bridge.onContext(context => apply(Boolean(context.isDark)));
    // Deployed theme.js owns the toggle; follow AstrBot only for initial context.
  });
})();
