import {esc, icon} from './ui.js';

export const protectionNames = {off: '关闭', recommended: '推荐严格保护', custom: '自定义'};
export const evidenceNames = {verified: '已验证', unverified: '未验证', unsupported: '不支持', failed: '异常', checking: '检测中', stale: '已过期'};
const scopes = {service: '本服务', client: '整个客户端', browser: '当前浏览器'};
export function evidenceBadge(state) {
  return `<span class="badge ${state === 'verified' ? 'success' : ['failed', 'stale'].includes(state) ? 'warn' : ''}">${evidenceNames[state] || '未验证'}</span>`;
}
export function protectionSelector(capability, selected) {
  return `<fieldset class="protection-presets"><legend>保护预设</legend>${capability.presets.map(p => `<label class="preset-option" title="${esc(p.reason || '')}"><input type="radio" name="protection-preset" value="${p.id}" ${selected === p.id ? 'checked' : ''} ${p.supported ? '' : 'disabled'} aria-describedby="protection-help">${esc(p.label)}</label>`).join('')}</fieldset><p id="protection-help" class="hint">完整保护尚未开放。关闭额外保护后，所选出口保持不变；指定 ISP 仍拒绝 UDP。</p>`;
}
export function protectionRows(rows, policy, exitKind) {
  return rows.map(row => {
    const mandatory = row.id === 'udp' && exitKind === 'isp';
    const value = row.id === 'webrtc' ? 'unmanaged' : mandatory ? 'block' : policy.preset === 'off' ? 'inherit' : policy.overrides[row.id] || row.recommended;
    const reason = mandatory ? '指定 ISP 的基础约束，关闭额外保护仍保留。' : row.id === 'udp' ? '仅约束本服务的 UDP 流量。' : row.reason;
    return `<div class="protection-item"><div class="detail-heading"><h4>${esc(row.label)}</h4><span class="scope-tag">${scopes[row.scope]}</span></div><dl class="value-pair"><div><dt>推荐值</dt><dd>${esc(row.options[row.recommended])}</dd></div><div><dt>配置值</dt><dd>${esc(row.options[value])}</dd></div></dl><p class="hint">${esc(reason)}</p></div>`;
  }).join('') + '<p class="hint scope-note">DNS 与 IPv6 的变更可能影响其他服务；启用前需要确认共用范围。配置值不代表已在客户端生效。</p>';
}
export function verificationContent(verification) {
  const current = verification || {state: 'unverified', reason: '服务检测尚未接入。', items: []};
  const checked = current.checked_at ? new Date(current.checked_at * 1000).toLocaleString('zh-CN', {hour12: false}) : '尚未检测';
  return `<p class="hint">${esc(current.reason || '')}</p><div class="probe-list">${current.items.map(item => `<div class="probe-item"><span>${esc(item.label)}${item.reason && item.state !== 'unverified' ? `<small>${esc(item.reason)}</small>` : ''}</span>${evidenceBadge(item.state)}</div>`).join('')}</div><p class="hint">本服务路径 · ${esc(checked)}<br>其他应用与系统流量不在此检测范围内。</p>`;
}
export function probeSettings(settings) {
  return `<details id="probe-settings-details" class="editor-section"><summary>${icon('settings')}高级设置<span class="summary-note">检测参数</span></summary><p class="hint">使用推荐值，服务检测接入后生效。</p><dl class="value-pair"><div><dt>自动检测频率</dt><dd>${settings.interval_seconds / 60} 分钟</dd></div><div><dt>单次超时</dt><dd>${settings.timeout_seconds} 秒</dd></div></dl><p class="hint">${esc(settings.reason)}</p></details>`;
}
