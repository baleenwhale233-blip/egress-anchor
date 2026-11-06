import {esc, icon} from './ui.js';

const labels = {not_connected: '未检查', not_found: '未发现', discovered: '已发现', unsupported: '不支持读取', unavailable: '读取失败', multiple: '待选择', checking: '检查中…'};
const runtimeLabels = {reachable: '内核可读', unreachable: '无法连接内核', unconfirmed: '运行状态未确认', unsupported: '不支持读取运行信息'};
const modes = {rule: '规则模式', global: '全局模式', direct: '直连模式'};

export function clientSummary(app) {
  const client = app.state.client, source = app.state.sources.find(s => s.selected);
  const name = client.profile?.name || source?.name || '未选择';
  return `<span class="client-segment"><span class="status-dot"></span><strong>${esc(client.name || '客户端')} · ${app.clientLoading ? labels.checking : labels[client.state] || '未确认'}</strong></span><span class="client-segment"><span class="muted">${client.profile ? '客户端订阅' : '本地来源'}：</span><span class="source-name" title="${esc(name)}">${esc(name)}</span></span><span class="client-segment client-access"><strong class="muted">${client.state === 'discovered' ? '只读查看' : '尚未接入'}</strong></span><a href="#settings" class="button client-link">管理客户端 ${icon('chevron-right')}</a>`;
}
export function clientPanel(app) {
  const client = app.state.client, runtime = client.runtime || {};
  const checked = client.checked_at ? new Date(client.checked_at * 1000).toLocaleString('zh-CN', {hour12: false}) : '尚未检查';
  return `<div class="panel-heading"><h2>客户端</h2><span class="badge">${app.clientLoading ? labels.checking : labels[client.state] || '未确认'}</span></div><div class="setting-row"><div><h3>${esc(client.name || 'FlClash')}${client.version ? ` ${esc(client.version)}` : ''}</h3><p>${esc(client.reason)}</p></div><button id="refresh-client" ${app.clientLoading ? 'disabled' : ''}>${icon('refresh-cw')}重新检查</button></div>
    ${client.state === 'discovered' ? `<dl class="client-facts"><div><dt>当前订阅</dt><dd>${esc(client.profile?.name || '尚未选择')}</dd></div><div><dt>运行信息</dt><dd>${runtimeLabels[runtime.state] || '未确认'}${runtime.mode ? ` · ${modes[runtime.mode]}` : ''}${typeof runtime.tun === 'boolean' ? ` · TUN ${runtime.tun ? '开启' : '关闭'}` : ''}</dd></div></dl>${runtime.reason ? `<p class="hint">${esc(runtime.reason)}</p>` : ''}` : ''}
    ${client.state === 'multiple' ? `<ul class="client-candidates">${client.candidates.map(c => `<li>${esc(c.name)} · ${esc(c.profile?.name || labels[c.state])}</li>`).join('')}</ul>` : ''}
    <p class="hint">最近检查：${esc(checked)}。只读取客户端信息，服务是否生效仍需应用与路径验证。</p><div id="client-error" class="error-notice" role="alert">${esc(app.clientError || '')}</div>`;
}
export function bindClient(app, root) {
  root.querySelector('#refresh-client')?.addEventListener('click', () => app.refreshClient());
}
export function updateClientViews(app) {
  const summary = document.querySelector('[data-client-summary]');
  if (summary) summary.innerHTML = clientSummary(app);
  const panel = document.querySelector('[data-client-panel]');
  if (panel) {
    const focused = panel.contains(document.activeElement);
    panel.innerHTML = clientPanel(app); bindClient(app, panel);
    if (focused && !app.clientLoading) panel.querySelector('#refresh-client').focus();
  }
}
