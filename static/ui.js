/** Shared, native controls; all interpolated user content must pass esc(). */
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const icon = name => `<svg class="icon" aria-hidden="true"><use href="/static/assets/lucide.svg#${name}"></use></svg>`;
export const exits = [['isp', '指定 ISP'], ['airport', '仅走机场'], ['direct', '直连'], ['inherit', '沿用订阅']];
export function exitOptions(name, selected, disabled = false) {
  return `<fieldset class="exit-options" aria-label="出口方式" ${disabled ? 'disabled' : ''}>${exits.map(([value, label]) =>
    `<label class="exit-option"><input type="radio" name="${esc(name)}" value="${value}" ${selected === value ? 'checked' : ''}>${label}</label>`).join('')}</fieldset>`;
}
export function references(state, kind) {
  return kind === 'isp' ? state.landings.map(x => ({id: x.id, name: x.name})) : state.airport_choices.map(name => ({id: name, name}));
}
export function referenceOptions(state, choice) {
  const values = references(state, choice.kind);
  const missing = choice.reference && !values.some(value => value.id === choice.reference);
  return `<option value="">${missing ? '引用已失效，请重新选择' : values.length ? '请选择' : choice.kind === 'isp' ? '请先添加落地代理' : '当前订阅没有可用节点'}</option>` +
    values.map(x => `<option value="${esc(x.id)}" ${x.id === choice.reference ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
}
let toastTimer;
export function toast(message) {
  const box = document.querySelector('#toast');
  clearTimeout(toastTimer); box.textContent = message; box.hidden = false;
  toastTimer = setTimeout(() => { box.hidden = true; }, 4200);
}
const drawer = document.querySelector('#drawer');
let opener, openerId, openerName, openerValue, scrollY = 0;
export function openDrawer(title, content, footer) {
  if (!drawer.open) {
    opener = document.activeElement; openerId = opener?.id;
    openerName = opener?.name; openerValue = opener?.value; scrollY = window.scrollY;
    document.documentElement.classList.add('modal-open');
    document.body.style.setProperty('--scroll-offset', `-${scrollY}px`);
    document.body.classList.add('modal-open');
  }
  drawer.innerHTML = `<header class="drawer-header"><h2 id="drawer-title">${esc(title)}</h2><button type="button" class="icon-button subtle" data-close aria-label="关闭">${icon('x')}</button></header>
    <div class="drawer-content">${content}</div><footer class="drawer-footer">${footer}</footer>`;
  drawer.removeAttribute('aria-busy');
  drawer.oncancel = null;
  drawer.querySelectorAll('[data-close]').forEach(button => { button.onclick = closeDrawer; });
  if (!drawer.open) drawer.showModal();
  (drawer.querySelector('[autofocus]') || drawer.querySelector('[data-close]')).focus();
  return drawer;
}
export function closeDrawer() { if (drawer.getAttribute('aria-busy') !== 'true') drawer.close(); }
drawer.addEventListener('cancel', event => { if (drawer.getAttribute('aria-busy') === 'true') event.preventDefault(); });
drawer.addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const controls = [...drawer.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')]
    .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
  const first = controls[0], last = controls.at(-1), active = document.activeElement;
  if (!first) { event.preventDefault(); return; }
  if (event.shiftKey && (active === first || !drawer.contains(active))) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && (active === last || !drawer.contains(active))) {
    event.preventDefault(); first.focus();
  }
});
drawer.addEventListener('close', () => {
  document.documentElement.classList.remove('modal-open'); document.body.classList.remove('modal-open');
  document.body.style.removeProperty('--scroll-offset'); window.scrollTo(0, scrollY);
  const target = opener?.isConnected ? opener : (openerId && document.getElementById(openerId)) ||
    [...document.querySelectorAll('main input')].find(el => el.name === openerName && el.value === openerValue);
  (target || document.querySelector('[data-nav][aria-current]'))?.focus({preventScroll: true});
});
export function formError(dialog, error) {
  const notice = dialog.querySelector('[role="alert"]');
  notice.textContent = error.message;
  dialog.querySelectorAll('[aria-invalid]').forEach(x => { x.removeAttribute('aria-invalid'); x.removeAttribute('aria-describedby'); });
  const field = error.field || '';
  const name = field.includes('exclude_domains') ? 'exclude_domains' : /domains/.test(field) ? 'extra_domains' : field.endsWith('.name') ? 'name' : field.includes('reference') ? 'reference' : null;
  const input = name && dialog.querySelector(`[name="${name}"]`);
  if (input) {
    input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', 'form-error');
    input.closest('details')?.setAttribute('open', ''); input.focus();
  } else { notice.tabIndex = -1; notice.focus(); }
}
export async function submitDialog(dialog, work) {
  if (dialog.getAttribute('aria-busy') === 'true') return;
  dialog.setAttribute('aria-busy', 'true');
  const controls = [...dialog.querySelectorAll('button,input,select,textarea')].filter(x => !x.disabled);
  controls.forEach(x => { x.disabled = true; });
  let failure;
  try { await work(); }
  catch (error) { failure = error; }
  finally {
    dialog.removeAttribute('aria-busy'); controls.forEach(x => { x.disabled = false; });
  }
  if (failure) formError(dialog, failure);
  return !failure;
}
export const errorSlot = '<div id="form-error" class="error-notice" role="alert"></div>';
export const cancelButton = '<button type="button" data-close>取消</button>';
export function serviceSavedMessage(result, state) {
  if (result.compiled) return '服务设置已保存，请在客户端更新本地订阅。';
  return state.selected_source_id ? '设置已保存。请修复其他服务的失效引用后生成配置。' : '服务设置已保存，导入订阅后可生成配置。';
}
