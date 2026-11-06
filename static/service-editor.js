import {esc, icon, exitOptions, referenceOptions, openDrawer, closeDrawer, submitDialog, errorSlot, cancelButton, toast, serviceSavedMessage} from './ui.js';
import {openLandingEditor} from './resources.js';
const splitDomains = value => value.split(/[\s,，]+/).map(x => x.trim()).filter(Boolean);

export function openServiceEditor(app, original = null, draft = original) {
  const savedSource = app.state.selected_source_id;
  draft = draft || {name: '', template_id: null, extra_domains: [], exclude_domains: [], exit: {kind: 'inherit'}, strict: false};
  const builtin = Boolean(draft.template_id);
  const template = app.state.templates[draft.template_id];
  const domainField = (name, label, hint) => `<label class="field"><span class="field-label">${label}</span><textarea name="${name}" rows="4" spellcheck="false" placeholder="example.com">${esc((draft[name] || []).join('\n'))}</textarea><span class="hint">${hint}</span></label>`;
  const dialog = openDrawer(original ? `${original.name} · 设置` : '添加服务', `<form id="service-form">${errorSlot}
    <label class="field"><span class="field-label">服务名称</span><input name="name" maxlength="80" required autofocus autocomplete="off" value="${esc(draft.name)}" placeholder="例如：开发工具"></label>
    <div class="field"><span class="field-label">出口方式</span>${exitOptions('editor-exit', draft.exit.kind)}</div>
    <div id="editor-reference"></div>
    ${!builtin ? domainField('extra_domains', '服务域名', '每行一个域名，包含其子域名。不要填写网址或通配符。') : ''}
    <div class="detail-block"><div class="strict-setting"><h3>严格模式</h3><span class="switch" role="switch" aria-checked="false" aria-disabled="true" aria-label="严格模式，暂不可用"></span></div><p class="hint">完整严格模式尚未开放。当前指定 ISP 的服务只允许 TCP，UDP 会被拒绝；DNS 与 WebRTC 仍未验证。</p></div>
    <details ${!builtin && draft.exclude_domains?.length ? 'open' : ''}><summary>高级设置 · 域名范围</summary>${builtin ? `<p class="hint">${esc(template.description)} · 模板 ${esc(app.state.template_version)}</p><div class="domain-chips">${template.domains.map(d => `<span>${esc(d)}</span>`).join('')}</div>${domainField('extra_domains', '额外域名', '与模板域名合并；每行一个。')}` : ''}${domainField('exclude_domains', '排除域名', '从本服务中排除，交回原订阅处理，不会强制直连。')}</details>
    </form>`, `${original && !builtin ? '<button type="button" class="subtle danger" id="delete-service">删除服务</button>' : ''}${cancelButton}<button type="submit" form="service-form" class="solid">保存设置</button>`);
  let selectedExit = {...draft.exit};
  const form = dialog.querySelector('form');
  function collect() {
    return {...draft, name: form.elements.name.value, exit: {...selectedExit}, strict: false,
      extra_domains: splitDomains(form.elements.extra_domains.value), exclude_domains: splitDomains(form.elements.exclude_domains.value)};
  }
  function renderReference() {
    const target = dialog.querySelector('#editor-reference'), kind = selectedExit.kind;
    if (!['isp', 'airport'].includes(kind)) {
      target.innerHTML = `<p class="hint" style="margin:0 0 22px">${kind === 'direct' ? '此服务将不经过代理。' : '此服务继续使用原订阅的路由规则。'}</p>`;
      return;
    }
    target.innerHTML = `<div class="field"><div class="field-action"><label for="editor-ref">${kind === 'isp' ? '落地代理' : '机场节点或策略组'}</label>${kind === 'isp' ? `<button type="button" class="subtle" id="quick-add-landing">${icon('plus')}添加代理</button>` : ''}</div><select id="editor-ref" name="reference" required>${referenceOptions(app.state, selectedExit)}</select><p class="hint">${kind === 'isp' ? '通过当前订阅的节点连接此落地代理。' : '仅列出可确认不含直连的固定节点或策略组。动态节点组与旧版接管的组暂不支持。'}</p></div>`;
    target.querySelector('select').onchange = event => { selectedExit.reference = event.target.value; };
    target.querySelector('#quick-add-landing')?.addEventListener('click', () => {
      const pending = collect();
      openLandingEditor(app, null, landing => openServiceEditor(app, original, {...pending, exit: landing ? {kind: 'isp', reference: landing.id} : pending.exit}));
    });
  }
  form.querySelectorAll('[name="editor-exit"]').forEach(input => { input.onchange = () => {
    selectedExit = {kind: input.value};
    if (selectedExit.kind === draft.exit.kind && draft.exit.reference) selectedExit.reference = draft.exit.reference;
    renderReference();
  }; });
  renderReference();
  form.onsubmit = async event => {
    event.preventDefault();
    const next = collect();
    let result;
    const ok = await submitDialog(dialog, async () => { result = await app.saveService(original, next, savedSource); });
    if (ok) { closeDrawer(); toast(serviceSavedMessage(result, app.state)); }
  };
  dialog.querySelector('#delete-service')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    if (!button.dataset.confirm) { button.dataset.confirm = 'true'; button.textContent = '确认删除'; return; }
    let result;
    const ok = await submitDialog(dialog, async () => { result = await app.saveService(original, original, savedSource, true); });
    if (ok) { closeDrawer(); toast(result.compiled ? '服务已删除。更新客户端订阅后，其域名将由原订阅处理。' : app.state.selected_source_id ? '服务已删除。请修复其他服务的失效引用后生成配置。' : '服务已删除。'); }
  });
}
