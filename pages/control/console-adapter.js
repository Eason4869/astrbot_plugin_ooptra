/* Adapt Ooptra's existing console to AstrBot's authenticated, sandboxed view. */
(() => {
  'use strict';
  const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
  const values = new Map();
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
    const parsed = parse(path);
    // Ooptra's token is attached by the plugin backend, never by the browser.
    delete parsed.params.token;
    return bridge.apiPost('ui/console', {...parsed, method: opts.method || 'GET', body: opts.body || {}});
  }

  class LogSource {
    constructor(path) {
      this.listeners = new Map();
      this.closed = false;
      this.subscription = null;
      this.retryTimer = null;
      this.params = parse(path).params;
      this.connect();
    }
    async connect() {
      try {
        await ready;
        if (this.closed) return;
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
      this.closed = true;
      clearTimeout(this.retryTimer);
      if (this.subscription) bridge.unsubscribeSSE(this.subscription).catch(() => {});
    }
  }

  async function openLink(path) {
    if (path.startsWith('/api/logs/tail')) {
      await ready;
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

  window.OoptraPanel = {api, storage, EventSource: LogSource, openLink};
  document.addEventListener('DOMContentLoaded', () => {
    const buttons = document.querySelectorAll('[data-theme-toggle]');
    function apply(dark) {
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      buttons.forEach(button => button.setAttribute('aria-checked', String(dark)));
    }
    if (bridge) bridge.onContext(context => apply(Boolean(context.isDark)));
    buttons.forEach(button => button.addEventListener('click', () => apply(document.documentElement.dataset.theme !== 'dark')));
  });
})();
