import {esc, icon, exitOptions, references, referenceOptions, openDrawer, closeDrawer, toast, serviceSavedMessage} from './ui.js';
import {openServiceEditor} from './service-editor.js';
const application = {inherited: '沿用订阅', saved: '已保存', pending_client: '待客户端更新'};

function serviceCard(app, service) {
  const {id, name, exit} = service, busy = app.pending.has(id), error = app.errors.get(id);
  const missing = ['isp', 'airport'].includes(exit.kind) && !references(app.state, exit.kind).some(value => value.id === exit.reference);
  const brand = {claude: 'claude', chatgpt: 'openai', deepseek: 'deepseek'}[service.template_id];
  const mark = brand ? `<img src="/static/assets/${brand}.svg" alt="">` : icon('network');
  const reference = ['isp', 'airport'].includes(exit.kind)
    ? `<div class="reference-line"><label class="exit-caption" for="reference-${id}">${exit.kind === 'isp' ? '落地选择' : '机场选择'}</label><select id="reference-${id}" data-reference="${id}" ${busy ? 'disabled' : ''}>${referenceOptions(app.state, exit)}</select></div>`
    : `<p class="exit-note">${exit.kind === 'direct' ? '不经过代理' : '使用原订阅的路由规则'}</p>`;
  return `<article class="service-card" data-service="${id}" aria-labelledby="title-${id}" ${busy ? 'aria-busy="true"' : ''}>
    <div class="service-title"><span class="service-mark ${esc(service.template_id || 'custom')}">${mark}</span><h2 id="title-${id}">${esc(name)}</h2>${['claude', 'chatgpt'].includes(service.template_id) ? '<span class="strict-badge">严格模式：关闭</span>' : ''}</div>
    <div class="service-body"><div class="exit-form"><div class="exit-line"><span class="exit-caption" id="exit-label-${id}">出口方式</span>${exitOptions(`exit-${id}`, exit.kind, busy)}</div>${reference}</div>
    <div class="service-status"><div class="status-line"><span class="status-label">应用：</span><span class="badge ${service.application === 'pending_client' ? 'warn' : ''}" role="status">${busy ? '保存中…' : application[service.application]}</span></div><div class="status-line"><span class="status-label">最近检测：</span><span class="badge">未验证</span></div><p class="status-scope">检测范围：本服务路径</p></div>
    <div class="service-actions"><button id="probe-${id}" class="primary" data-probe="${id}" aria-label="${esc(name)} 检测详情">${icon('refresh-cw')}检测详情</button><button id="edit-${id}" data-edit="${id}" aria-label="${esc(name)} 设置" ${busy ? 'disabled' : ''}>${icon('settings')}设置</button></div></div>
    ${missing ? `<div class="error-notice" role="alert">${icon('circle-alert')}<span>原来的${exit.kind === 'isp' ? '落地代理' : '机场选择'}已不可用，请重新选择后保存。本地订阅保留上一份有效配置。</span></div>` : ''}
    ${error ? `<div class="error-notice" role="alert">${icon('circle-alert')}<span>${esc(error)}</span><button type="button" class="subtle" data-retry="${id}">重试</button></div>` : ''}</article>`;
}
export function renderServices(app) {
  const source = app.state.sources.find(x => x.selected);
  return `<header class="page-heading"><h1>服务出口</h1><button id="add-service" class="primary">${icon('plus')}添加服务</button></header>
    <section class="client-summary" aria-label="客户端与订阅"><span class="client-segment"><span class="status-dot"></span><strong>客户端 · 未接入</strong></span><span class="client-segment"><span class="muted">订阅：</span><span class="source-name" title="${esc(source?.name || '')}">${esc(source?.name || '未选择')}</span></span><span class="client-segment client-access"><span class="muted">接入：</span><strong class="muted">待接入</strong></span><a href="#settings" class="button client-link">管理客户端 ${icon('chevron-right')}</a></section>
    <section class="service-list" aria-label="服务列表">${app.state.services.map(s => serviceCard(app, s)).join('')}</section>
    <p class="workspace-note">${icon('circle-alert')}<span>${source ? '配置保存后会生成本地订阅；客户端自动接入与服务检测尚未开放。' : '先到设置中导入订阅，即可生成服务出口配置。当前可以先保存服务设置。'}</span></p>`;
}
export function bindServices(app, root) {
  const find = id => app.state.services.find(s => s.id === id);
  root.querySelector('#add-service').onclick = () => openServiceEditor(app);
  root.querySelectorAll('[data-edit]').forEach(button => { button.onclick = () => openServiceEditor(app, find(button.dataset.edit)); });
  root.querySelectorAll('[data-probe]').forEach(button => { button.onclick = () => openProbe(find(button.dataset.probe)); });
  root.querySelectorAll('.exit-options input').forEach(input => { input.onchange = () => {
    const service = find(input.closest('[data-service]').dataset.service);
    if (['isp', 'airport'].includes(input.value)) {
      const next = {kind: input.value};
      app.render(); openServiceEditor(app, service, {...service, exit: next});
    } else quickSave(app, service, {kind: input.value});
  }; });
  root.querySelectorAll('[data-reference]').forEach(select => { select.onchange = () => {
    if (select.value) quickSave(app, find(select.dataset.reference), {kind: find(select.dataset.reference).exit.kind, reference: select.value});
  }; });
  root.querySelectorAll('[data-retry]').forEach(button => { button.onclick = () => {
    const service = find(button.dataset.retry); openServiceEditor(app, service, app.drafts.get(service.id));
  }; });
}
async function quickSave(app, service, exit) {
  const draft = {...service, exit};
  app.drafts.set(service.id, draft);
  try { const result = await app.saveService(service, draft); app.drafts.delete(service.id); toast(serviceSavedMessage(result, app.state)); }
  catch (error) { app.errors.set(service.id, error.message); app.render(); }
}
function openProbe(service) {
  const dialog = openDrawer(`${service.name} · 检测详情`, `<div class="detail-block"><h3>尚未验证服务路径</h3><p class="hint">客户端尚未接入，暂时无法确认服务实际经过的出口。保存或生成配置不会标记为检测通过。</p></div><div class="probe-list">${['实际出口', '代理链连接', '服务可达性', 'DNS 路径', 'UDP / WebRTC'].map(label => `<div class="probe-item"><span>${label}</span><span class="badge">未验证</span></div>`).join('')}</div><p class="hint">这里只检查本服务的访问路径。其他应用与系统流量不在此范围内。</p>`, '<button type="button" data-close>关闭</button><button type="button" class="primary" id="probe-settings">管理客户端</button>');
  dialog.querySelector('#probe-settings').onclick = () => { closeDrawer(); location.hash = 'settings'; };
}
