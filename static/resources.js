import {esc, icon, openDrawer, closeDrawer, submitDialog, errorSlot, cancelButton, toast} from './ui.js';
function usage(state, id) {
  return [...state.services.filter(s => s.exit.kind === 'isp' && s.exit.reference === id).map(s => s.name),
    ...state.bindings.filter(b => b.enabled && b.landing_id === id).map(b => `旧版策略 ${b.group}`)];
}
export function renderResources(app) {
  return `<header class="page-heading"><h1>落地代理</h1><button id="add-landing" class="primary">${icon('plus')}添加代理</button></header>
    ${app.state.landings.length ? `<section class="resource-list" aria-label="落地代理列表">${app.state.landings.map(landing => {
      const used = usage(app.state, landing.id);
      return `<article class="resource-row"><span class="service-mark custom">${icon('layers')}</span><div class="resource-info"><h2>${esc(landing.name)}</h2><p>${esc(landing.type.toUpperCase())} · ${esc(landing.server)}:${landing.port}</p><div class="resource-usage">${used.length ? `使用中：${used.map(esc).join('、')}` : '尚未被服务使用'}</div></div><div class="resource-actions"><button id="landing-edit-${landing.id}" data-edit-landing="${landing.id}" aria-label="编辑 ${esc(landing.name)}">${icon('pencil')}编辑</button><button data-delete-landing="${landing.id}" class="subtle icon-button danger" ${used.length ? 'disabled title="请先移除此代理的引用"' : ''} aria-label="删除 ${esc(landing.name)}">${icon('trash-2')}</button></div></article>`;
    }).join('')}</section>` : `<div class="empty-state">${icon('layers')}<h2>还没有落地代理</h2><p>添加 HTTP 或 SOCKS5 代理后，可为服务指定固定出口。</p><button class="primary" data-add-landing>${icon('plus')}添加第一个代理</button></div>`}
    <p class="workspace-note">${icon('shield')}<span>代理凭据只保存在本机，已有密码不会回传到页面。</span></p>`;
}
export function bindResources(app, root) {
  root.querySelector('#add-landing').onclick = () => openLandingEditor(app);
  root.querySelector('[data-add-landing]')?.addEventListener('click', () => openLandingEditor(app));
  root.querySelectorAll('[data-edit-landing]').forEach(b => { b.onclick = () => openLandingEditor(app, app.state.landings.find(x => x.id === b.dataset.editLanding)); });
  root.querySelectorAll('[data-delete-landing]').forEach(b => { b.onclick = () => deleteLanding(app, app.state.landings.find(x => x.id === b.dataset.deleteLanding)); });
}
export function openLandingEditor(app, original = null, onReturn = null) {
  const landing = original || {name: '', type: 'socks5', server: '', port: 1080, username: ''};
  const field = (name, label, type = 'text', attrs = '') => `<label class="field"><span class="field-label">${label}</span><input name="${name}" type="${type}" value="${esc(landing[name] || '')}" ${attrs}></label>`;
  const dialog = openDrawer(original ? '编辑落地代理' : '添加落地代理', `<form id="landing-form">${errorSlot}
    ${field('name', '代理名称', 'text', 'required maxlength="80" autofocus placeholder="例如：美国住宅 01"')}
    <label class="field"><span class="field-label">代理地址</span><input name="url" autocomplete="off" spellcheck="false" placeholder="socks5://用户名:密码@服务器:端口"><span class="hint">可粘贴完整代理地址，或在下方手动填写。</span></label>
    <details ${original ? 'open' : ''}><summary>手动填写代理信息</summary><label class="field"><span class="field-label">代理类型</span><select name="type"><option value="socks5" ${landing.type === 'socks5' ? 'selected' : ''}>SOCKS5</option><option value="http" ${landing.type === 'http' ? 'selected' : ''}>HTTP</option></select></label>
    <div class="form-grid">${field('server', '服务器', 'text', 'autocomplete="off" spellcheck="false"')}${field('port', '端口', 'number', 'min="1" max="65535"')}</div>
    ${field('username', '用户名', 'text', 'autocomplete="off"')}<label class="field"><span class="field-label">密码</span><input name="password" type="password" autocomplete="new-password" placeholder="${landing.has_password ? '留空保留已保存的密码' : '无密码可留空'}"><span class="hint">${landing.has_password ? '密码已保存；留空保留，输入可替换。' : '仅用于连接此代理。'}</span></label></details></form>`, `${onReturn ? '<button type="button" id="landing-back">返回服务设置</button>' : cancelButton}<button class="solid" type="submit" form="landing-form">保存代理</button>`);
  if (onReturn) {
    const back = () => onReturn(null);
    dialog.querySelector('#landing-back').onclick = back;
    dialog.querySelector('[data-close]').onclick = back;
    dialog.oncancel = event => { event.preventDefault(); if (dialog.getAttribute('aria-busy') !== 'true') back(); };
  }
  const form = dialog.querySelector('form');
  form.onsubmit = async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const payload = {...values, port: Number(values.port), udp: original?.udp ?? true};
    if (original) payload.id = original.id;
    let result;
    const ok = await submitDialog(dialog, async () => { result = await app.write('/api/landings', 'POST', payload); });
    if (ok) { if (onReturn) onReturn(result.landing); else closeDrawer(); toast('落地代理已保存。'); }
  };
}
function deleteLanding(app, landing) {
  const dialog = openDrawer('删除落地代理', `${errorSlot}<p>删除「${esc(landing.name)}」后，需要重新添加才能使用。</p>`, `${cancelButton}<button id="confirm-delete" class="danger">删除代理</button>`);
  dialog.querySelector('#confirm-delete').onclick = async () => {
    const ok = await submitDialog(dialog, () => app.write(`/api/landings/${encodeURIComponent(landing.id)}`, 'DELETE'));
    if (ok) { closeDrawer(); toast('落地代理已删除。'); }
  };
}
