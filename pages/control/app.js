import './session-manager.js';
'use strict';

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const $ = id => document.getElementById(id);
const state = {bindings: [], areas: [], channels: [], status: null, area: '', channel: '', busy: false, online: false, allowJoin: true};
let channelRequest = 0;
let memberRequest = 0;
let refreshing = false;
const labels = {gemini_live: 'Gemini Live', mimo_cascade: 'MiMo 级联'};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function feedback(message, tone = 'ok') {
  $('feedback').hidden = false;
  $('feedback').dataset.tone = tone;
  $('feedback').textContent = message;
}
function empty(host, text, columns = 0) {
  if (columns) {
    const row = element('tr');
    const cell = element('td', text, 'empty');
    cell.colSpan = columns;
    row.append(cell);
    host.replaceChildren(row);
  } else host.replaceChildren(element('p', text, 'hint'));
}
function buttons() {
  $('join').disabled = state.busy || !state.online || !state.allowJoin || !state.area || !state.channel;
  $('leave').disabled = state.busy || !state.online || !state.allowJoin || !state.status?.joined;
  $('switch-backend').disabled = state.busy || !state.online;
  $('visit-on').disabled = $('visit-off').disabled = state.busy || !state.online || !state.area;
  $('refresh').disabled = state.busy || refreshing;
}
function connection(ok, detail) {
  state.online = ok;
  $('connection').dataset.tone = ok ? 'ok' : 'err';
  $('connection-title').textContent = ok ? 'Ooptra 已连接' : '暂时无法连接 Ooptra';
  $('connection-detail').textContent = detail;
  $('connection-chip').textContent = ok ? '在线' : '离线';
  $('connection-chip').className = 'chip ' + (ok ? 'ok' : 'err');
  buttons();
}
function setOptions(select, rows, placeholder) {
  const previous = select.value;
  select.replaceChildren(new Option(placeholder, ''));
  rows.forEach(row => select.add(new Option(row.name || row.id, row.id)));
  if (rows.some(row => row.id === previous)) select.value = previous;
}
let sessionManager = null;
function renderBindings() {
  if (!sessionManager) sessionManager = window.OoptraSessionManager.mount($('session-manager'), {
    rpc: body => body.action === 'list' ? bridge.apiGet('ui/bootstrap') : bridge.apiPost(
      body.action === 'save' ? 'ui/binding' : body.action === 'delete' ? 'ui/unbind' : 'ui/sources', body),
    channels: area => bridge.apiGet('ui/channels', {area}), onChange: applyBootstrap,
  });
  sessionManager.update(state.bootstrap || {}, state.areas);
}
function applyBootstrap(data) {
  state.bootstrap = data;
  state.bindings = data.bindings;
  state.allowJoin = data.allow_join;
  renderContext(data.context_sync || {});
  setOptions($('group-preset'), state.bindings.map(row => ({id: row.group_id, name: `${row.label || 'QQ 群'} · ${row.group_id}`})), '手动选择房间');
  renderBindings();
  buttons();
}
function renderContext(data) {
  state.context = data;
  setOptions($('context-persona'), data.personas || [], '跟随旧绑定会话人格');
  $('context-enabled').value = String(data.enabled === true);
  if (data.persona_id && !Array.from($('context-persona').options).some(o => o.value === data.persona_id)) {
    $('context-persona').add(new Option(`已不可用：${data.persona_id}`, data.persona_id));
  }
  $('context-persona').value = data.persona_id || '';
  const provider = $('context-provider');
  provider.replaceChildren(new Option('自动识别', 'auto'), new Option('只同步人格', 'none'));
  (data.providers || []).forEach(row => provider.add(new Option(`${row.name} ${row.version}${row.available ? '' : '（接口未就绪）'}`, row.id)));
  if (data.memory_provider && !Array.from(provider.options).some(o => o.value === data.memory_provider)) provider.add(new Option(`未启用：${data.memory_provider}`, data.memory_provider));
  provider.value = data.memory_provider || 'auto';
  $('context-provider-detail').textContent = data.memory_error || '已识别记忆插件，语音使用相同的记忆来源。';
  $('context-chip').textContent = data.enabled ? '等待语音上下文' : '未启用';
}
async function saveContext(refresh = false) {
  const button = $(refresh ? 'refresh-context' : 'save-context');
  button.disabled = true;
  try {
    const payload = refresh ? {action: 'refresh'} : {enabled: $('context-enabled').value === 'true', persona_id: $('context-persona').value, memory_provider: $('context-provider').value};
    const result = await bridge.apiPost('ui/context', payload);
    if (!refresh) applyBootstrap(result);
    feedback(result.message || '当前语音上下文已刷新，请查看生效状态。');
    await refreshStatus();
  } catch (error) {feedback(error.message, 'err');}
  finally {button.disabled = false;}
}
async function loadBootstrap() {
  applyBootstrap(await bridge.apiGet('ui/bootstrap'));
}
async function loadAreas() {
  try {
    const data = await bridge.apiGet('ui/areas');
    if (!Array.isArray(data.areas)) throw new Error('Ooptra 返回的域列表格式不正确。');
    state.areas = data.areas.filter(row => row && typeof row.id === 'string');
  } catch (error) {
    const ids = [...new Set(state.bindings.flatMap(row => row.areas))];
    state.areas = ids.map(id => ({id, name: id}));
    feedback(`域列表读取失败：${error.message}\n可以从群绑定选择，或手动填写域 ID。`, 'warn');
  }
  setOptions($('area-select'), state.areas, '选择一个 Oopz 域');
  renderBindings();
  await sessionManager.loadChannels();
}
async function refreshStatus() {
  if (refreshing) return;
  refreshing = true;
  buttons();
  try {
    const data = await bridge.apiGet('ui/status');
    const context = data.astrbot_context;
    if (context) {
      $('context-chip').textContent = context.active ? '已接入' : (context.state === 'offline' ? '桥接未连接' : '使用本地设置');
      $('context-detail').textContent = [context.persona_umo || (context.group_id ? `旧群 ${context.group_id}` : '尚未确定会话来源'), context.persona_id ? `人格 ${context.persona_id}` : '', context.provider ? `记忆 ${context.provider} · ${(context.memory_umos || []).length} 个读写会话` : '未接入记忆', `待写入 ${context.pending_writes || 0} 条`, context.error || ''].filter(Boolean).join(' · ');
    }
    const backendChanged = state.status?.backend !== data.backend;
    state.status = data;
    connection(true, data.joined ? '语音会话正在运行，房间操作会应用到当前实例。' : '语音会话尚未进房，可以选择频道后进入。');
    $('room-state').textContent = data.joined ? '已在房' : '未进房';
    $('room-detail').textContent = data.joined ? `${data.area || '—'} / ${data.channel || '—'}` : '选择域和频道后进入语音';
    $('backend-name').textContent = labels[data.backend] || data.backend || '未返回方案';
    $('voice-enabled').textContent = data.enabled === false ? '语音对话未启用；切换方案不会自动开启' : '连接同一实例的群共用方案';
    if (backendChanged && !state.busy && document.activeElement !== $('backend-select') && data.backend in labels) $('backend-select').value = data.backend;
    if (!state.area && (data.area || data.default_area)) {
      await selectArea(data.area || data.default_area, data.channel || data.default_channel || '');
    }
  } catch (error) {
    connection(false, error.message);
    $('room-state').textContent = '状态未知';
    $('room-detail').textContent = '恢复连接后刷新状态';
    $('backend-name').textContent = '状态未知';
  } finally { refreshing = false; buttons(); }
}
async function selectArea(area, preferred = '') {
  state.area = area;
  state.channel = '';
  memberRequest += 1;
  empty($('members'), '正在选择频道…', 3);
  $('member-count').textContent = '未选择频道';
  if (area && !Array.from($('area-select').options).some(option => option.value === area)) $('area-select').add(new Option(area, area));
  $('area-select').value = area;
  await loadChannels(preferred);
}
async function loadChannels(preferred = state.channel) {
  const request = ++channelRequest;
  const area = state.area;
  state.channel = '';
  state.channels = [];
  buttons();
  if (!area) return empty($('channels'), '选择一个域后查看语音频道。');
  empty($('channels'), '正在读取语音频道…');
  try {
    const data = await bridge.apiGet('ui/channels', {area});
    if (request !== channelRequest) return;
    if (!Array.isArray(data.channels)) throw new Error('Ooptra 返回的频道列表格式不正确。');
    state.channels = data.channels.filter(row => row && typeof row.id === 'string');
    const fallback = state.status?.default_area === area ? state.status.default_channel : '';
    state.channel = state.channels.find(row => row.id === (preferred || fallback))?.id || state.channels[0]?.id || '';
    $('channel-count').textContent = `${state.channels.length} 个频道`;
    renderChannels();
    await loadMembers();
  } catch (error) {
    if (request !== channelRequest) return;
    empty($('channels'), error.message);
    $('channel-count').textContent = '读取失败';
    empty($('members'), '频道读取失败，请刷新频道。', 3);
  } finally { buttons(); }
}
function renderChannels() {
  $('channels').replaceChildren();
  if (!state.channels.length) empty($('channels'), '这个域还没有可用的语音频道。');
  state.channels.forEach(row => {
    const button = element('button', undefined, 'channel-option');
    button.type = 'button';
    button.dataset.channel = row.id;
    button.setAttribute('aria-pressed', String(row.id === state.channel));
    button.title = row.id;
    button.append(element('b', row.name || row.id), element('span', `${row.count ?? '—'} 人在线`));
    button.addEventListener('click', () => {state.channel = row.id; renderChannels(); loadMembers();});
    $('channels').append(button);
  });
  const target = state.channels.find(row => row.id === state.channel);
  $('target-hint').textContent = target ? `已选择：${target.name || target.id}` : '选择语音频道后进房。';
  buttons();
}
function muted(row, kind) {
  const explicit = kind === 'mic' ? row.mic_muted : row.speaker_muted;
  const flag = kind === 'mic' ? row.m : row.hm;
  if (typeof explicit === 'boolean') return explicit;
  if (flag === 0 || flag === 1) return flag === 1;
  return typeof row[kind] === 'boolean' ? !row[kind] : null;
}
async function loadMembers() {
  const request = ++memberRequest;
  const area = state.area, channel = state.channel;
  if (!area || !channel) return empty($('members'), '选择语音频道后查看房间成员。', 3);
  try {
    const data = await bridge.apiGet('ui/members', {area, channel});
    if (request !== memberRequest) return;
    if (!Array.isArray(data.members)) throw new Error('Ooptra 返回的成员列表格式不正确。');
    $('members').replaceChildren();
    $('member-count').textContent = `${data.members.length} 人`;
    if (!data.members.length) empty($('members'), '这个频道暂时没有成员。', 3);
    data.members.forEach(row => {
      const tr = element('tr');
      tr.append(element('td', row.name || row.uid || '未命名成员'));
      for (const kind of ['mic', 'speaker']) {
        const value = muted(row, kind);
        const td = element('td');
        td.append(element('span', value === null ? '未知' : value ? (kind === 'mic' ? '闭麦' : '闭听') : '开启', 'chip ' + (value === null ? '' : value ? 'warn' : 'ok')));
        tr.append(td);
      }
      $('members').append(tr);
    });
  } catch (error) {
    if (request !== memberRequest) return;
    empty($('members'), error.message, 3);
    $('member-count').textContent = '读取失败';
  }
}
async function operate(body) {
  if (state.busy) return;
  state.busy = true;
  buttons();
  feedback('正在应用操作…', 'warn');
  try {
    const result = await bridge.apiPost('ui/action', body);
    const notes = Array.isArray(result.result?.notes) ? result.result.notes.slice(0, 8).map(String) : [];
    feedback([result.message, ...notes].join('\n'), result.applied === false ? 'warn' : 'ok');
    if (body.action === 'auto_visit') $('visit-detail').textContent = body.enabled ? '这个域的串门开关已开启。' : '这个域的串门开关已关闭。';
    await refreshStatus();
  } catch (error) {feedback(error.message, 'err');}
  finally {state.busy = false; buttons();}
}
function navigate(view) {
  const bindings = view === 'bindings';
  $('view-voice').hidden = bindings;
  $('view-bindings').hidden = !bindings;
  $('page-title').textContent = bindings ? '会话对应' : '语音台';
  $('page-sub').textContent = bindings ? '多个完整 UMO 可映射到同一范围，人格单选，记忆多选并写回' : '查看房间、控制语音与切换对话方案';
  document.querySelectorAll('[data-view]').forEach(button => {
    const active = button.dataset.view === (bindings ? 'bindings' : 'voice');
    button.classList.toggle('is-active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  location.hash = bindings ? 'bindings' : 'voice';
}
async function start() {
  $('ooptra-project').addEventListener('click', event => {
    event.preventDefault();
    $('project-copy-status').textContent = '可复制地址，也可选中后手动复制。';
    $('project-dialog').showModal();
    $('project-url').select();
  });
  $('copy-project-url').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('project-url').value);
      $('project-copy-status').textContent = 'GitHub 链接已复制。';
    } catch (_) {
      $('project-url').focus();
      $('project-url').select();
      $('project-copy-status').textContent = document.execCommand('copy') ? 'GitHub 链接已复制。' : '地址已选中，请按 Ctrl+C 或长按复制。';
    }
  });
  if (!bridge) {connection(false, '请从 AstrBot 插件详情打开工作台。'); return;}
  await bridge.ready();
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.view)));
  window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));
  navigate(location.hash.slice(1));
  $('area-select').addEventListener('change', () => { $('group-preset').value = ''; selectArea($('area-select').value); });
  $('group-preset').addEventListener('change', () => {
    const row = state.bindings.find(binding => binding.group_id === $('group-preset').value);
    if (row) selectArea(row.areas.includes(state.status?.default_area) ? state.status.default_area : row.areas[0] || '', row.channel);
  });
  $('load-manual-area').addEventListener('click', () => selectArea($('manual-area').value.trim()));
  $('refresh-channels').addEventListener('click', () => loadChannels());
  $('refresh-members').addEventListener('click', loadMembers);
  $('refresh').addEventListener('click', async () => {
    try {await loadBootstrap(); await loadAreas(); await refreshStatus(); await loadChannels();}
    catch (error) {feedback(error.message, 'err');}
  });
  $('join').addEventListener('click', () => operate({action: 'join', area: state.area, channel: state.channel}));
  $('leave').addEventListener('click', () => operate({action: 'leave'}));
  $('switch-backend').addEventListener('click', () => operate({action: 'backend', backend: $('backend-select').value}));
  $('save-context').addEventListener('click', () => saveContext());
  $('refresh-context').addEventListener('click', () => saveContext(true));
  $('visit-on').addEventListener('click', () => operate({action: 'auto_visit', area: state.area, enabled: true}));
  $('visit-off').addEventListener('click', () => operate({action: 'auto_visit', area: state.area, enabled: false}));
  await loadBootstrap();
  await loadAreas();
  await refreshStatus();
  const timer = setInterval(() => {
    if (!document.hidden && !state.busy) {refreshStatus(); if (!$('view-voice').hidden) loadMembers();}
  }, 5000);
  window.addEventListener('beforeunload', () => clearInterval(timer), {once: true});
}
start().catch(error => connection(false, error.message));
