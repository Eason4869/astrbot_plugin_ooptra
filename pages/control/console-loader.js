/* Preserve AstrBot's bridge and message listeners while mounting deployed code. */
(() => {
  'use strict';
  const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
  const loadingBody = document.body.innerHTML;
  const loadingHead = document.head.innerHTML;
  const extraStyle = document.querySelector('link[data-console-extra]')?.cloneNode();
  let started = false;

  async function load() {
    if (started) return;
    started = true;
    let mounted = false;
    try {
      if (!bridge) throw new Error('请从 AstrBot 插件详情打开控制台。');
      await bridge.ready();
      const payload = await bridge.apiGet('ui/console-page');
      if (typeof payload.html !== 'string' || !Array.isArray(payload.scripts) || !payload.scripts.every(source => typeof source === 'string')) throw new Error('控制台页面响应不完整。');
      const page = new DOMParser().parseFromString(payload.html, 'text/html');
      document.head.replaceChildren(...page.head.childNodes);
      if (extraStyle) document.head.append(extraStyle);
      document.body.replaceChildren(...page.body.childNodes);
      mounted = true;
      for (const attribute of page.documentElement.attributes) document.documentElement.setAttribute(attribute.name, attribute.value);
      const back = document.createElement('a');
      back.href = './index.html';
      back.className = 'btn ghost';
      back.textContent = '返回插件工作台';
      (document.getElementById('page-tools')?.parentElement || document.body).prepend(back);
      let scriptError;
      const captureError = event => {
        scriptError = event.error || new Error(event.message || '控制台脚本无法运行。');
        event.preventDefault();
      };
      window.addEventListener('error', captureError);
      try {
        for (const source of payload.scripts) {
          const script = document.createElement('script');
          script.textContent = source;
          document.body.append(script);
          if (scriptError) throw new Error('部署中的控制台脚本不兼容：' + scriptError.message);
        }
        // Upstream boot handlers register after the shell's DOMContentLoaded.
        document.dispatchEvent(new Event('DOMContentLoaded'));
        if (scriptError) throw new Error('部署中的控制台初始化失败：' + scriptError.message);
      } finally { window.removeEventListener('error', captureError); }
    } catch (error) {
      window.__ooptraBootFailed = true;
      window.OoptraPanel?.stop();
      // Keep nodes addressable for an already-started upstream async boot catch.
      const failedDocument = document.createElement('div');
      failedDocument.hidden = true;
      if (mounted) failedDocument.append(...document.body.childNodes);
      document.head.innerHTML = loadingHead;
      document.body.innerHTML = loadingBody;
      document.body.append(failedDocument);
      document.getElementById('console-load-title').textContent = '无法加载部署中的控制台';
      document.getElementById('console-load-detail').textContent = error.message || '请检查 Ooptra WebUI 地址和访问令牌。';
      document.getElementById('console-retry').addEventListener('click', () => location.reload());
    }
  }
  document.addEventListener('DOMContentLoaded', load, {once: true});
})();
