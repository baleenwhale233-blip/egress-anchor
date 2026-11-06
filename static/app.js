import {request} from './api.js';
import {esc, icon, toast} from './ui.js';
import {renderServices, bindServices} from './services.js';
import {renderResources, bindResources} from './resources.js';
import {renderSettings, bindSettings} from './settings.js';
import {updateClientViews} from './client.js';
const main = document.querySelector('#main');
const pages = {services: ['服务出口', renderServices, bindServices], landings: ['落地代理', renderResources, bindResources], settings: ['设置', renderSettings, bindSettings]};
const editableFields = ['id', 'name', 'template_id', 'extra_domains', 'exclude_domains', 'exit', 'protection'];
let refreshSequence = 0, loadError = null;
const app = {
  state: null, pending: new Set(), errors: new Map(), drafts: new Map(),
  client: null, clientLoading: false, clientError: null,
  route: () => Object.hasOwn(pages, location.hash.slice(1)) ? location.hash.slice(1) : 'services',
  render() {
    const route = this.route(), [title, render, bind] = pages[route];
    document.title = `${title} · 出口锚点 Egress Anchor`;
    document.querySelectorAll('[data-nav]').forEach(link => {
      if (link.dataset.nav === route) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    const focus = document.activeElement;
    const focusId = focus?.id, focusName = focus?.name, focusValue = focus?.value;
    if (!this.state) {
      main.innerHTML = `<header class="page-heading"><h1>${title}</h1></header><div class="error-notice" role="alert">${icon('circle-alert')}<span>${esc(loadError || '正在加载…')}</span><button id="reload-state">重试</button></div>`;
    } else {
      main.innerHTML = render(this) + (loadError ? `<div class="error-notice" role="alert">${icon('circle-alert')}<span>${esc(loadError)}</span><button id="reload-state">重试</button></div>` : '');
      bind(this, main);
    }
    main.querySelector('#reload-state')?.addEventListener('click', () => this.refresh().catch(() => {}));
    if (focus && main.contains(document.activeElement) === false && !document.querySelector('#drawer').open) {
      const restored = focusId ? document.getElementById(focusId) : focusName ? [...main.querySelectorAll('input')].find(x => x.name === focusName && x.value === focusValue) : null;
      restored?.focus({preventScroll: true});
    }
  },
  async refresh() {
    const sequence = ++refreshSequence;
    try {
      const result = await request('/api/state');
      if (sequence === refreshSequence) {
        this.state = result.state;
        if (this.client) this.state.client = this.client;
        loadError = null; this.render();
      }
    } catch (error) {
      if (sequence === refreshSequence) { loadError = error.message; this.render(); }
      throw error;
    }
  },
  async refreshClient() {
    if (this.clientLoading || !this.state) return;
    this.clientLoading = true; this.clientError = null; updateClientViews(this);
    try {
      const result = await request('/api/client', {timeout: 6000});
      this.client = this.state.client = result.client;
    } catch (error) { this.clientError = error.message; }
    finally { this.clientLoading = false; updateClientViews(this); }
  },
  async write(path, method, data) {
    const result = await request(path, {method, data});
    try { await this.refresh(); }
    catch { toast('操作已保存，但列表刷新失败。请重试刷新。'); }
    return result;
  },
  async saveService(original, draft, source = this.state.selected_source_id, remove = false) {
    const id = original?.id;
    if (id && this.pending.has(id)) throw new Error('此服务正在保存，请稍候。');
    if (id) { this.pending.add(id); this.errors.delete(id); this.render(); }
    const service = Object.fromEntries(editableFields.filter(key => draft[key] !== undefined).map(key => [key, draft[key]]));
    try {
      return await this.write('/api/services', remove ? 'DELETE' : original ? 'PUT' : 'POST', {
        service, source_id: source, revision: original?.revision ?? 0,
      });
    } finally { if (id) this.pending.delete(id); this.render(); }
  },
};
window.addEventListener('hashchange', () => { app.render(); window.scrollTo(0, 0); });
document.querySelector('.skip-link').addEventListener('click', event => {
  event.preventDefault(); main.focus(); main.scrollIntoView({block: 'start'});
});
app.refresh().then(() => app.refreshClient()).catch(() => {});
