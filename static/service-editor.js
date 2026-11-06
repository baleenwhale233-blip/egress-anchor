import {esc, icon, exitOptions, referenceOptions, openDrawer, closeDrawer, guardDrawerClose, submitDialog, errorSlot, cancelButton, toast, serviceSavedMessage} from './ui.js';
import {openLandingEditor} from './resources.js';
import {protectionSelector, protectionRows, evidenceBadge, verificationContent, probeSettings} from './service-details.js';
const splitDomains = value => [...new Set(value.split(/[\s,，]+/).map(x => x.trim()).filter(Boolean))];
const blank = () => ({name: '', template_id: null, extra_domains: [], exclude_domains: [], exit: {kind: 'inherit'}, protection: {preset: 'off', overrides: {}}});
const comparable = record => ({name: record.name.trim(), exit: record.exit,
  protection: record.protection || {preset: 'off', overrides: {}},
  extra_domains: [...(record.extra_domains || [])].sort(), exclude_domains: [...(record.exclude_domains || [])].sort()});

export function openServiceEditor(app, original = null, draft = original, savedSource = app.state.selected_source_id) {
  draft = draft || blank();
  const builtin = Boolean(draft.template_id), template = app.state.templates[draft.template_id];
  const capabilities = app.state.capabilities.protection, initial = comparable(original || blank());
  const brand = {claude: 'claude', chatgpt: 'openai', deepseek: 'deepseek'}[draft.template_id];
  const mark = brand ? `<img src="/static/assets/${brand}.svg" alt="">` : icon('network');
  let selectedExit = {...draft.exit}, protection = structuredClone(draft.protection || blank().protection);
  const verification = original?.status?.verification;
  const domainField = (name, label, hint) => `<label class="field"><span class="field-label">${label}</span><textarea name="${name}" rows="3" spellcheck="false" placeholder="example.com">${esc((draft[name] || []).join('\n'))}</textarea><span class="hint">${hint}</span></label>`;
  const dialog = openDrawer(original ? `${original.name} · 服务设置` : '添加服务', `
    <div id="editor-dirty" class="editor-dirty" role="status"></div>
    <p class="runtime-summary">${icon('circle-alert')}<span>当前运行：尚未确认本服务出口</span></p>
    <div id="discard-notice" class="discard-notice" hidden><p>有修改尚未保存。要继续编辑还是放弃修改？</p><div><button type="button" id="keep-editing">继续编辑</button><button type="button" id="discard-changes" class="danger">放弃修改</button></div></div>
    <form id="service-form">${errorSlot}
    <section class="editor-section service-identity"><div class="editor-identity"><span class="service-mark ${esc(draft.template_id || 'custom')}">${mark}</span><label class="field"><span class="field-label">服务名称</span><input name="name" maxlength="80" required autofocus autocomplete="off" value="${esc(draft.name)}" placeholder="例如：开发工具"></label><span class="scope-tag">${builtin ? '内置模板' : '自定义'}</span></div>
    <div class="field"><span class="field-label">出口方式</span>${exitOptions('editor-exit', draft.exit.kind)}</div><div id="editor-reference"></div></section>
    <section class="editor-section">${protectionSelector(capabilities, protection.preset)}</section>
    <details id="protection-details" class="editor-section"><summary>${icon('shield')}保护详情<span class="summary-note">推荐值与配置值</span></summary><div id="protection-rows"></div></details>
    <details id="domain-details" class="editor-section" ${!builtin ? 'open' : ''}><summary>${icon('network')}服务域名与排除范围<span class="summary-note">${builtin ? '模板预填' : '自定义范围'}</span></summary>
    ${builtin ? `<p class="hint">${esc(template.description)} · 模板 ${esc(app.state.template_version)}</p><div class="domain-chips">${template.domains.map(d => `<span>${esc(d)}</span>`).join('')}</div>` : ''}
    ${domainField('extra_domains', builtin ? '额外域名' : '服务域名', builtin ? '与模板合并，每行一个，包含子域名。' : '每行一个域名，包含子域名；不要填写网址或通配符。')}
    ${domainField('exclude_domains', '排除域名', '交回原订阅处理，不会强制直连。')}</details>
    <details id="verification-details" class="editor-section"><summary>${icon('refresh-cw')}检测结果${evidenceBadge(verification?.state || 'unverified')}</summary>${verificationContent(verification || app.state.services[0]?.status?.verification)}</details>
    ${probeSettings(capabilities.probe_settings)}</form>`,
    `${original && !builtin ? '<button type="button" class="subtle danger" id="delete-service">删除服务</button>' : `<span class="footer-copy">${savedSource ? '保存后生成本地配置' : '保存到本地，导入订阅后可生成配置'}</span>`}${cancelButton}<button type="submit" form="service-form" class="solid">保存设置</button>`);
  const form = dialog.querySelector('form');
  function collect() {
    return {...draft, name: form.elements.name.value, exit: {...selectedExit}, protection,
      extra_domains: splitDomains(form.elements.extra_domains.value), exclude_domains: splitDomains(form.elements.exclude_domains.value)};
  }
  function changeCount() {
    const next = comparable(collect());
    return Object.keys(initial).filter(key => JSON.stringify(initial[key]) !== JSON.stringify(next[key])).length;
  }
  function updateChanges() {
    const count = changeCount(), banner = dialog.querySelector('#editor-dirty');
    banner.classList.toggle('has-changes', count > 0);
    banner.innerHTML = `${icon(count ? 'pencil' : 'check')}<span>${count ? `${count} 项修改待保存` : original ? '与已保存的设置一致' : '填写服务设置'}</span>`;
  }
  function renderProtection() {
    dialog.querySelector('#protection-rows').innerHTML = protectionRows(capabilities.items, protection, selectedExit.kind);
  }
  function renderReference() {
    const target = dialog.querySelector('#editor-reference'), kind = selectedExit.kind;
    if (!['isp', 'airport'].includes(kind)) {
      target.innerHTML = `<p class="hint exit-explanation">${kind === 'direct' ? '此服务将不经过代理。' : '此服务继续使用原订阅的路由规则。'}</p>`;
      return;
    }
    target.innerHTML = `<div class="field"><div class="field-action"><label for="editor-ref">${kind === 'isp' ? '落地代理' : '机场节点或策略组'}</label></div><select id="editor-ref" name="reference" required>${referenceOptions(app.state, selectedExit)}</select><p class="hint">${kind === 'isp' ? '通过当前订阅的节点连接此落地代理。' : '仅列出可确认不含直连的固定节点或策略组。'}</p>${kind === 'isp' ? `<button type="button" class="subtle quick-add" id="quick-add-landing">${icon('plus')}快速添加落地</button>` : ''}</div>`;
    target.querySelector('select').onchange = event => { selectedExit.reference = event.target.value; updateChanges(); };
    target.querySelector('#quick-add-landing')?.addEventListener('click', () => {
      const pending = collect();
      openLandingEditor(app, null, landing => openServiceEditor(app, original,
        {...pending, exit: landing ? {kind: 'isp', reference: landing.id} : pending.exit}, savedSource));
    });
  }
  form.querySelectorAll('[name="editor-exit"]').forEach(input => { input.onchange = () => {
    selectedExit = {kind: input.value};
    if (selectedExit.kind === draft.exit.kind && draft.exit.reference) selectedExit.reference = draft.exit.reference;
    renderReference(); renderProtection(); updateChanges();
  }; });
  form.querySelectorAll('[name="protection-preset"]').forEach(input => { input.onchange = () => {
    protection = {preset: input.value, overrides: {}}; renderProtection(); updateChanges();
  }; });
  form.addEventListener('input', updateChanges);
  renderReference(); renderProtection(); updateChanges(); guardDrawerClose(() => changeCount() > 0);
  form.onsubmit = async event => {
    event.preventDefault();
    const next = collect();
    let result;
    const ok = await submitDialog(dialog, async () => { result = await app.saveService(original, next, savedSource); });
    if (ok) { closeDrawer({force: true}); toast(serviceSavedMessage(result, app.state)); }
  };
  dialog.querySelector('#delete-service')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    if (!button.dataset.confirm) { button.dataset.confirm = 'true'; button.textContent = '确认删除'; return; }
    let result;
    const ok = await submitDialog(dialog, async () => { result = await app.saveService(original, original, savedSource, true); });
    if (ok) { closeDrawer({force: true}); toast(result.compiled ? '服务已删除。更新客户端订阅后，其域名将由原订阅处理。' : '服务已删除。'); }
  });
}
