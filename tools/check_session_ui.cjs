/* Run against tools/webui_preview.py: real AstrBot sandbox bridge, isolated data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.OOPTRA_PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.OOPTRA_BROWSER_EXECUTABLE || undefined});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}}), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = process.env.OOPTRA_PLUGIN_PREVIEW || 'http://127.0.0.1:18768';
  try {
    await page.request.post(base + '/test/reset');
    await page.goto(base);
    const frame = page.frameLocator('iframe');
    await frame.locator('#connection-title').filter({hasText: '已连接'}).waitFor();
    await frame.locator('[data-view=bindings]').click();
    const sm = frame.locator('#session-manager');
    await sm.locator('[data-list]').getByText('开黑群', {exact: true}).waitFor();
    await sm.locator('[data-range]').selectOption('area');
    for (const [platform, kind, name] of [['OopzWS', 'oopz', '文字闲聊'], ['QQ', 'friend', '演示联系人']]) {
      await sm.locator('[data-pick]').click();
      const dialog = frame.locator('dialog.sm-dialog');
      await dialog.locator('[data-platform]').selectOption(platform);
      await dialog.locator('[data-kind]').selectOption(kind);
      await dialog.locator('[data-load]').click();
      await dialog.locator('[data-choices] button').filter({hasText: name}).click();
      await sm.locator('[data-control]').uncheck();
      await sm.locator('[data-map-form] button[type=submit]').click();
      await sm.locator('[data-list]').getByText(name, {exact: true}).waitFor();
    }
    await sm.locator('[data-tab=sources]').click();
    await sm.locator('[data-persona-umo]').selectOption('QQ:GroupMessage:123456789');
    await sm.locator('[data-persona-id]').selectOption('游戏搭子');
    for (const checkbox of await sm.locator('[data-memories] input').all()) await checkbox.check();
    assert.equal(await sm.locator('[data-save-source]').isDisabled(), true);
    await sm.locator('[data-private]').check();
    await sm.locator('[data-save-source]').click();
    await sm.locator('[data-rules]').getByText('记忆查询并写回 3 个会话', {exact: true}).waitFor();
    const saved = (await (await page.request.get(base + '/test/state')).json()).config.context_sources.rules[0];
    assert.deepEqual(saved.memory_umos, ['QQ:GroupMessage:123456789', 'OopzWS:GroupMessage:789', 'QQ:FriendMessage:456']);
    assert.equal(saved.persona_id, '游戏搭子');
    assert.equal(saved.allow_private, true);
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      await page.evaluate(() => window.scrollTo(0, 0));
      await sm.locator('[data-tab=sources]').scrollIntoViewIfNeeded();
      await sm.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await sm.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'plugin source overflow ' + width);
      if (process.env.OOPTRA_BROWSER_ARTIFACTS) {
        fs.mkdirSync(process.env.OOPTRA_BROWSER_ARTIFACTS, {recursive: true});
        await page.screenshot({path: path.join(process.env.OOPTRA_BROWSER_ARTIFACTS, 'sessions-' + width + '.png'), fullPage: true});
      }
    }
    await frame.locator('#full-console').click();
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    await frame.locator('#screen-app .nav-item[data-page=voice]').click();
    await frame.locator('#voice-tabs [data-tab=bindings]').click();
    await frame.locator('#session-manager [data-tab=sources]').click();
    await frame.locator('#session-manager [data-rules]').getByText('记忆查询并写回 3 个会话', {exact: true}).waitFor();
    await frame.getByRole('link', {name: '返回插件工作台', exact: true}).click();
    await frame.locator('#connection-title').filter({hasText: '已连接'}).waitFor();
    assert.deepEqual(errors, []);
    console.log('PASS plugin session UI: named QQ/Oopz/private choices, full UMO, persona override, three memory read/write targets, consent, shared console, 1440/390/320, no page errors');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
