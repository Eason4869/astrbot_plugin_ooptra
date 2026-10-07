/* Browser integration checks against tools/webui_preview.py's mock host.
 * node tools/webui_smoke.cjs http://127.0.0.1:18765 /path/to/node_modules/playwright
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[3] || 'playwright');
const url = process.argv[2] || 'http://127.0.0.1:18765';
const output = path.resolve(__dirname, '../.webui-evidence');
fs.mkdirSync(output, {recursive: true});

(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1080}, reducedMotion: 'reduce'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 400 && ['stylesheet', 'script', 'image'].includes(response.request().resourceType())) {
      errors.push('resource HTTP ' + response.status() + ': ' + new URL(response.url()).pathname);
    }
  });
  const frame = page.frameLocator('iframe');
  async function capture(filename) {
    await page.frames()[1].evaluate(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    // Edge can delay painting an opaque sandbox iframe after navigation.
    await page.waitForTimeout(1800);
    // The host iframe already fills the viewport. Full-page capture resizes it
    // and Edge can lose the sandbox compositor surface during that resize.
    await page.screenshot({path: path.join(output, filename)});
  }
  try {
    await page.request.post(url + '/test/reset');
    await page.goto(url);
    await frame.locator('#connection-title').filter({hasText: 'Ooptra 已连接'}).waitFor();
    await frame.locator('#members').filter({hasText: '小明'}).waitFor();
    assert.ok((await frame.locator('#members').innerText()).includes('未知'));
    await frame.locator('.dependency-notice').filter({hasText: '本插件需安装Ooptra方能完美运行'}).waitFor();
    assert.equal(await frame.locator('#ooptra-project').getAttribute('href'), 'https://github.com/Eason4869/Ooptra');
    await frame.locator('#ooptra-project').click();
    assert.equal(await frame.locator('#project-url').inputValue(), 'https://github.com/Eason4869/Ooptra');
    await frame.locator('#copy-project-url').click();
    await frame.locator('#project-copy-status').filter({hasText: /已复制|地址已选中/}).waitFor();
    await frame.locator('#project-dialog button[value="close"]').click();
    await capture('voice-desktop.png');

    await frame.locator('[data-view="bindings"]').click();
    await frame.locator('#add-binding').click();
    await frame.locator('#binding-group').fill('1122334455');
    await frame.locator('#binding-areas').fill('demo-area\ndemo-area-2');
    await frame.locator('#binding-label').fill('浏览器验证群');
    await frame.locator('#save-binding').click();
    await frame.locator('#bindings').filter({hasText: '1122334455'}).waitFor();
    await frame.getByRole('button', {name: '编辑群 1122334455', exact: true}).click();
    assert.equal(await frame.locator('#binding-areas').inputValue(), 'demo-area\ndemo-area-2');
    await frame.locator('#binding-label').fill('多域保存已验证');
    await frame.locator('#save-binding').click();
    await frame.locator('#bindings').filter({hasText: '多域保存已验证'}).waitFor();
    await page.reload();
    await frame.locator('[data-view="bindings"]').click();
    await frame.locator('#bindings').filter({hasText: '多域保存已验证'}).waitFor();
    const persisted = JSON.parse(fs.readFileSync(path.join(output, 'preview-config.json'), 'utf8'));
    assert.deepEqual(persisted.group_map['1122334455'].areas, ['demo-area', 'demo-area-2']);
    await capture('bindings-desktop.png');
    await frame.getByRole('button', {name: '移除群 1122334455', exact: true}).click();
    await frame.locator('#confirm-dialog button[value="confirm"]').click();
    await frame.locator('#bindings').filter({hasText: '1122334455'}).waitFor({state: 'hidden'});

    await frame.locator('[data-view="voice"]').click();
    await frame.locator('#leave').click();
    await frame.locator('#feedback').filter({hasText: '已退出语音'}).waitFor();
    await frame.locator('#backend-select').selectOption('mimo_cascade');
    await frame.locator('#refresh').click();
    await page.waitForTimeout(300);
    assert.equal(await frame.locator('#backend-select').inputValue(), 'mimo_cascade', 'status refresh must preserve the pending backend choice');
    await frame.locator('#switch-backend').click();
    await frame.locator('#feedback').filter({hasText: '操作过于频繁'}).waitFor();
    await page.waitForTimeout(3200);
    await frame.locator('#switch-backend').click();
    await frame.locator('#backend-name').filter({hasText: 'MiMo 级联'}).waitFor();

    await frame.locator('#full-console').click();
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    assert.equal(await frame.locator('#deployed-version').textContent(), 'deployment-one');
    await page.request.post(url + '/test/deployed', {data: {version: 'deployment-two'}});
    await page.frames()[1].evaluate(() => location.reload());
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    assert.equal(await frame.locator('#deployed-version').textContent(), 'deployment-two', 'refresh must load the deployed version, not a snapshot');
    await frame.locator('#verdict-title').filter({hasText: '链路正常'}).waitFor();
    await frame.locator('.rail-utilities > summary').click();
    await frame.locator('#btn-logout').click();
    await frame.locator('#dialog-yes').click();
    await frame.locator('#login-title').filter({hasText: '登录控制台'}).waitFor();
    await frame.locator('#login-token').fill('wrong-password');
    await frame.locator('#login-submit').click();
    await frame.locator('#login-msg').filter({hasText: '控制台密码不正确'}).waitFor();
    await frame.locator('#login-token').fill('preview-server-only-token');
    await frame.locator('#login-submit').click();
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    assert.equal(await frame.locator('#login-token').inputValue(), '');
    // Refreshing the full document must also initialize against the injected SDK.
    const fullFrame = page.frames()[1];
    await Promise.all([fullFrame.waitForNavigation({waitUntil: 'load'}), fullFrame.evaluate(() => location.reload())]);
    await frame.locator('#verdict-title').filter({hasText: '链路正常'}).waitFor();
    await capture('full-console-desktop.png');
    await frame.locator('button[data-page="logs"]').click();
    await frame.locator('#log-view').filter({hasText: 'AstrBot console bridge connected'}).waitFor();
    const downloadPromise = page.waitForEvent('download');
    await frame.locator('#log-download').click();
    const download = await downloadPromise;
    assert.equal(download.suggestedFilename(), 'ooptra-logs.txt');
    await download.saveAs(path.join(output, 'downloaded-logs.txt'));
    assert.ok(fs.readFileSync(path.join(output, 'downloaded-logs.txt'), 'utf8').includes('console bridge connected'));
    await frame.locator('button[data-page="config"]').click();
    await frame.locator('#config-groups').filter({hasText: 'OneBot v11 连接'}).waitFor();
    await frame.locator('#config-groups .cfg-switch input').first().uncheck();
    await frame.locator('#config-save-restart').click();
    await frame.locator('.toast').filter({hasText: '正在重新连接'}).waitFor();
    const saved = await (await page.request.get(url + '/test/state')).json();
    assert.ok(saved.calls.some(call => call.path === '/api/bridge/restart'));
    await frame.locator('button[data-page="voice"][data-tab="session"]').click();
    await frame.locator('#v-backend').filter({hasText: 'mimo_cascade'}).waitFor();
    await frame.locator('#v-joined').filter({hasText: '不在房'}).waitFor();
    await page.waitForTimeout(3200);
    await frame.locator('#voice-join').click();
    await frame.locator('#v-joined').filter({hasText: '在房'}).waitFor();
    await frame.locator('#v-target').filter({hasText: 'demo-area / demo-channel'}).waitFor();
    await frame.locator('#voice-tabs [data-tab="tools"]').click();
    await frame.locator('#diagnostic-run').click();
    await frame.locator('#diagnostic-results').filter({hasText: 'AstrBot diagnostics bridge verified'}).waitFor();
    await frame.locator('#preview-kind').selectOption('enter');
    await frame.locator('#preview-prompt option').filter({hasText: '在玩什么游戏'}).waitFor({state: 'attached'});
    await frame.locator('#preview-text').fill('AstrBot 独立试听验证');
    await frame.locator('#preview-run').click();
    await frame.locator('#preview-audio').waitFor({state: 'visible'});
    assert.ok((await frame.locator('#preview-audio').getAttribute('src')).startsWith('data:audio/wav;base64,'));
    await frame.locator('#preview-stop').click();
    await frame.locator('button[data-page="maintenance"]').click();
    await frame.locator('#maintenance-capability').filter({hasText: '支持自动升级'}).waitFor();
    await frame.locator('#maintenance-check').click();
    await frame.locator('#maintenance-check-detail').filter({hasText: '目标提交'}).waitFor();
    await frame.locator('#maintenance-backup').click();
    await frame.locator('#maintenance-backups').getByRole('link', {name: '下载'}).waitFor();
    const backupPromise = page.waitForEvent('download');
    await frame.locator('#maintenance-backups').getByRole('link', {name: '下载'}).click();
    const backup = await backupPromise;
    assert.equal(backup.suggestedFilename(), 'ooptra-' + 'a'.repeat(32) + '.zip');
    await backup.saveAs(path.join(output, 'downloaded-backup.zip'));
    assert.equal(fs.readFileSync(path.join(output, 'downloaded-backup.zip')).subarray(0, 2).toString(), 'PK');
    await frame.locator('#maintenance-update').click();
    await frame.locator('#dialog-no').click();
    let maintenanceState = await (await page.request.get(url + '/test/state')).json();
    assert.equal(maintenanceState.calls.some(call => call.path === '/api/maintenance/update'), false, 'cancelled update must not reach Ooptra');
    await frame.locator('#maintenance-update').click();
    await frame.locator('#dialog-yes').click();
    await frame.locator('#maintenance-detail').filter({hasText: '模拟维护已完成'}).waitFor();
    await frame.locator('#maintenance-backups').getByRole('button', {name: '恢复'}).click();
    await frame.locator('#dialog-yes').click();
    await frame.locator('#dialog').waitFor({state: 'hidden'});
    maintenanceState = await (await page.request.get(url + '/test/state')).json();
    assert.ok(maintenanceState.calls.some(call => call.path === '/api/maintenance/restore'));
    await frame.locator('button[data-page="account"]').click();
    await frame.locator('#page-account').filter({hasText: '138****0000'}).waitFor();
    await frame.getByRole('link', {name: '返回插件工作台'}).click();
    await frame.locator('#connection-title').filter({hasText: 'Ooptra 已连接'}).waitFor();

    await page.evaluate(() => send({kind: 'context', context: {isDark: true, locale: 'zh-CN'}}));
    await capture('voice-dark.png');
    await page.request.post(url + '/test/failure', {data: {offline: true}});
    await frame.locator('#refresh').click();
    await frame.locator('#connection-title').filter({hasText: '暂时无法连接'}).waitFor();
    assert.equal(await frame.locator('#join').isDisabled(), true);
    await frame.locator('[data-view="bindings"]').click();
    await frame.locator('#add-binding').click();
    await frame.locator('#binding-group').fill('99887766');
    await frame.locator('#binding-areas').fill('demo-area');
    await frame.locator('#save-binding').click();
    await frame.locator('#bindings').filter({hasText: '99887766'}).waitFor();
    await page.request.post(url + '/test/failure', {data: {offline: false}});

    await page.setViewportSize({width: 390, height: 844});
    await frame.locator('[data-view="voice"]').click();
    await frame.locator('#refresh').click();
    await frame.locator('#connection-title').filter({hasText: 'Ooptra 已连接'}).waitFor();
    let overflow = await page.frames()[1].evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false, 'quick panel overflows mobile viewport');
    await capture('voice-mobile.png');
    await frame.locator('#full-console').click();
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    overflow = await page.frames()[1].evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false, 'full console overflows mobile viewport');
    await capture('full-console-mobile.png');
    const anonymous = await page.request.post(url + '/api/v1/plugins/extensions/astrbot_plugin_ooptra/ui/action', {data: {action: 'leave'}});
    assert.equal(anonymous.status(), 401);
    const anonymousConsole = await page.request.get(url + '/api/v1/plugins/extensions/astrbot_plugin_ooptra/ui/console-page');
    assert.equal(anonymousConsole.status(), 401);
    await page.request.post(url + '/test/failure', {data: {offline: true}});
    await page.frames()[1].evaluate(() => location.reload());
    await frame.locator('#console-load-title').filter({hasText: '无法加载部署中的控制台'}).waitFor();
    await page.request.post(url + '/test/failure', {data: {offline: false}});
    await frame.locator('#console-retry').click();
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    await page.request.post(url + '/test/deployed', {data: {broken_script: true}});
    await page.frames()[1].evaluate(() => location.reload());
    await frame.locator('#console-load-title').filter({hasText: '无法加载部署中的控制台'}).waitFor();
    await frame.locator('#console-load-detail').filter({hasText: 'incompatible deployed script'}).waitFor();
    await page.request.post(url + '/test/deployed', {data: {broken_script: false}});
    await frame.locator('#console-retry').click();
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    await page.request.post(url + '/test/deployed', {data: {broken_script: 'domcontentloaded'}});
    await page.frames()[1].evaluate(() => location.reload());
    await frame.locator('#console-load-title').filter({hasText: '无法加载部署中的控制台'}).waitFor();
    await frame.locator('#console-load-detail').filter({hasText: '初始化失败'}).waitFor();
    await page.request.post(url + '/test/deployed', {data: {broken_script: false}});
    await frame.locator('#console-retry').click();
    await frame.locator('#screen-app').waitFor({state: 'visible'});
    assert.deepEqual(errors, [], 'unexpected browser errors');
    fs.writeFileSync(path.join(output, 'browser-result.json'), JSON.stringify({passed: true, errors, checks: ['sandbox bootstrap', 'member unknown state', 'binding add/edit/delete and disk persistence', 'offline binding save', 'voice leave', 'shared cooldown', 'backend switch', 'full console navigation and voice join', 'named log SSE and download', 'save config and reconnect', 'diagnostics and isolated WAV preview', 'saved preview prompts', 'maintenance check, backup and authenticated ZIP download', 'update cancellation and confirmed update/restore', 'configuration and account views', 'light/dark theme', 'mobile layouts', 'anonymous API rejected']}, null, 2));
    console.log('PASS: sandbox UI, binding persistence, voice controls, full console, logs, auth, themes and mobile layouts');
  } catch (error) {
    console.error('Browser errors:', JSON.stringify(errors));
    await page.screenshot({path: path.join(output, 'failed-check.png'), fullPage: true});
    throw error;
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
