import {esc, icon, openDrawer, closeDrawer, submitDialog, errorSlot, cancelButton, toast} from './ui.js';
import {clientPanel, bindClient} from './client.js';
export function renderSettings(app) {
  const state = app.state, source = state.sources.find(x => x.selected);
  const current = state.subscription.available && state.selected_source_id === state.active_source_id && !state.services_need_generation;
  return `<header class="page-heading"><h1>设置</h1></header><div class="settings-stack">
    <section class="panel" data-client-panel>${clientPanel(app)}</section>
    <section class="panel"><div class="panel-heading"><h2>订阅来源</h2><button id="add-source" class="primary">${icon('plus')}导入订阅</button></div>
    ${state.sources.length ? state.sources.map(s => `<div class="source-row"><div class="source-info"><strong>${esc(s.name)}</strong> ${s.selected ? '<span class="badge warn">当前选择</span>' : ''}<p>${s.proxy_count} 个节点 · ${s.group_count} 个策略组 · ${s.provider_count} 个节点提供方</p></div><div class="source-actions">${!s.selected ? `<button data-select-source="${s.id}">使用</button>` : ''}<button class="subtle icon-button" data-rename-source="${s.id}" aria-label="重命名 ${esc(s.name)}">${icon('pencil')}</button><button class="subtle icon-button danger" data-delete-source="${s.id}" ${s.active ? 'disabled title="正在提供本地订阅"' : ''} aria-label="删除 ${esc(s.name)}">${icon('trash-2')}</button></div></div>`).join('') : '<p class="hint">还没有订阅。请导入包含节点、策略组和规则的完整 Mihomo 配置。</p>'}</section>
    <section class="panel"><div class="panel-heading"><h2>本地订阅</h2><span class="badge ${current ? 'warn' : ''}">${current ? '已生成 · 待客户端更新' : state.subscription.available ? '仍在提供上一份配置' : '尚未生成'}</span></div><p class="hint">${source ? `当前选择：${esc(source.name)}。服务设置保存后会更新此订阅。` : '导入并选择订阅后，即可生成本地配置。'}复制地址后，在客户端中添加订阅并更新。</p>
    ${state.subscription.available ? `<div class="subscription-copy"><input id="subscription-url" aria-label="本地订阅地址" readonly value="${esc(state.subscription.url)}"><button id="copy-subscription">${icon('copy')}复制地址</button></div>` : ''}
    ${source ? `<button id="generate-subscription" class="primary" style="margin-top:18px">${icon('refresh-cw')}${current ? '重新生成' : '生成当前配置'}</button>` : ''}<div id="settings-error" role="alert"></div></section>
    <section class="panel"><div class="setting-row"><div><h3>旧版策略维护</h3><p>管理已有策略组绑定和连接诊断。</p></div><a class="button" href="/legacy">打开旧版 ${icon('arrow-up-right')}</a></div></section></div>`;
}
export function bindSettings(app, root) {
  bindClient(app, root);
  root.querySelector('#add-source').onclick = () => openSourceEditor(app);
  root.querySelectorAll('[data-rename-source]').forEach(b => { b.onclick = () => openSourceEditor(app, app.state.sources.find(x => x.id === b.dataset.renameSource)); });
  root.querySelectorAll('[data-delete-source]').forEach(b => { b.onclick = () => deleteSource(app, app.state.sources.find(x => x.id === b.dataset.deleteSource)); });
  root.querySelectorAll('[data-select-source]').forEach(b => { b.onclick = () => runSettings(app, b, async () => {
    await app.write(`/api/sources/${encodeURIComponent(b.dataset.selectSource)}/select`, 'PUT', {});
    toast('订阅已选择，请生成当前配置或保存服务设置。');
  }); });
  root.querySelector('#generate-subscription')?.addEventListener('click', event => runSettings(app, event.currentTarget, async () => {
    const service = app.state.services[0];
    const result = await app.saveService(service, service);
    if (!result.compiled) throw new Error('仍有服务引用已失效，请先回到服务出口重新选择。上一份本地配置已保留。');
    toast('本地订阅已生成，请在客户端更新。');
  }));
  root.querySelector('#copy-subscription')?.addEventListener('click', async () => {
    const input = root.querySelector('#subscription-url');
    try { await navigator.clipboard.writeText(input.value); toast('订阅地址已复制。'); }
    catch { input.focus(); input.select(); toast('请按 ⌘C 或 Ctrl+C 复制选中的地址。'); }
  });
}
async function runSettings(app, button, work) {
  button.disabled = true;
  try { await work(); }
  catch (error) {
    const notice = document.querySelector('#settings-error');
    if (notice) { notice.className = 'error-notice'; notice.textContent = error.message; }
  } finally { if (button.isConnected) button.disabled = false; }
}
function openSourceEditor(app, original = null) {
  const dialog = openDrawer(original ? '重命名订阅' : '导入订阅', `<form id="source-form">${errorSlot}<label class="field"><span class="field-label">订阅名称</span><input name="name" value="${esc(original?.name || '')}" maxlength="80" required autofocus placeholder="例如：日常订阅"></label>${!original ? '<label class="field"><span class="field-label">订阅地址</span><input name="url" type="url" required autocomplete="off" spellcheck="false" placeholder="https://example.com/subscription"><span class="hint">需要完整的 Mihomo 配置。地址仅用于本次导入，不会保存完整订阅地址。</span></label>' : ''}</form>`, `${cancelButton}<button class="solid" type="submit" form="source-form">${original ? '保存名称' : '保存并读取'}</button>`);
  const form = dialog.querySelector('form');
  form.onsubmit = async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const ok = await submitDialog(dialog, () => app.write(original ? `/api/sources/${encodeURIComponent(original.id)}` : '/api/sources', original ? 'PUT' : 'POST', values));
    if (ok) { closeDrawer(); toast(original ? '订阅名称已保存。' : '订阅已导入，可返回服务出口设置路由。'); }
  };
}
function deleteSource(app, source) {
  const dialog = openDrawer('删除订阅', `${errorSlot}<p>删除「${esc(source.name)}」及其本地快照？落地代理和服务设置会保留。</p>`, `${cancelButton}<button class="danger" id="confirm-delete">删除订阅</button>`);
  dialog.querySelector('#confirm-delete').onclick = async () => {
    const ok = await submitDialog(dialog, () => app.write(`/api/sources/${encodeURIComponent(source.id)}`, 'DELETE'));
    if (ok) { closeDrawer(); toast('订阅已删除。'); }
  };
}
