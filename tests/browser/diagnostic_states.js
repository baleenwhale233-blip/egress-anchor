// Run against tests/browser/modal_fixture.py using Playwright MCP.
// Only the disposable page is changed; all diagnostic responses are mocked.
async (page) => {
  const original = (await (await page.request.get(new URL('/api/state', page.url()).href)).json()).state;
  if (original.landings.length < 2) throw new Error('Use the disposable modal fixture');
  const makeState = (id) => ({
    ...structuredClone(original),
    selected_source_id: id, active_source_id: id,
    sources: [{ id, name: `Browser ${id}`, selected: true, active: true,
      managed_group_count: 1, group_count: 1, proxy_count: 1, imported_at: 1, display_url: '' }],
    catalog: { groups: [{ name: 'AI', type: 'select', proxies: ['front'], use: [] }],
      proxies: [{ name: 'front', type: 'http' }], providers: [] },
    bindings: [{ group: 'AI', enabled: true, landing_id: original.landings[0].id,
      front_hop: { mode: 'source' } }],
    subscription: { available: true, generation: `generation-${id}`, url: 'http://127.0.0.1/fixture-only',
      report: { counts: { managed_groups: 1 } } },
  });
  let current = makeState('fixture-a');
  let mode = 'delayed';
  let releaseLate;
  let started;
  let finished;
  const requestStarted = new Promise(resolve => { started = resolve; });
  const requestFinished = new Promise(resolve => { finished = resolve; });
  const lateResponse = new Promise(resolve => { releaseLate = resolve; });
  const report = {
    chain: { ok: true, ms: 12 }, selection: { ok: true },
    front: { ok: true, name: 'front', ms: 4 },
    landing: { ok: true, ip: '203.0.113.9', authentication: 'not_required', elapsed_ms: 20 },
    tested_at: 1,
  };
  await page.route('**/api/state', route => route.fulfill({ json: { ok: true, state: current } }));
  await page.route('**/api/bindings/probe', async route => {
    const selectedMode = mode;
    if (selectedMode === 'delayed') {
      started();
      await lateResponse;
      try { await route.fulfill({ json: { ok: true, probe: report } }); }
      catch { /* Changing the binding aborts this request in the browser. */ }
      finally { finished(); }
    } else if (selectedMode === 'stale') {
      current = makeState('fixture-b');
      await route.fulfill({ status: 409, json: { ok: false,
        error: { code: 'PROBE_STALE', message: 'fixture stale source' } } });
    } else {
      await route.fulfill({ json: { ok: true, probe: report } });
    }
  });
  try {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.locator('[data-probe="AI"]').click();
    await requestStarted;
    if (!(await page.locator('.probe-report').textContent()).includes('正在实测')) {
      throw new Error('The in-flight diagnostic state was not rendered');
    }
    await page.locator('[data-landing="AI"]').selectOption(original.landings[1].id);
    releaseLate();
    await requestFinished;
    await page.waitForFunction(() => !document.querySelector('.probe-row'));
    if (!await page.locator('[data-probe="AI"]').isDisabled()) {
      throw new Error('An unsaved changed binding remains testable');
    }

    mode = 'stale';
    await page.reload();
    await page.locator('[data-probe="AI"]').click();
    await page.waitForFunction(() => document.querySelector('#dock-msg').textContent.includes('配置已变化'));
    if (!(await page.locator('#source-list').textContent()).includes('Browser fixture-b')) {
      throw new Error('A stale diagnostic did not refresh the source');
    }
    if (await page.locator('.probe-row').count()) throw new Error('The stale result remains visible');

    mode = 'success';
    await page.locator('[data-probe="AI"]').click();
    await page.waitForFunction(() => document.querySelector('.probe-report')?.textContent.includes('203.0.113.9'));
    const text = await page.locator('.probe-report').textContent();
    if (!text.includes('本机直连验证') || !text.includes('整链延迟')) {
      throw new Error('Diagnostic scopes are missing');
    }
    return { cancelledResultDiscarded: true, staleSourceReloaded: true, completionRendered: true };
  } finally {
    releaseLate();
    await page.unrouteAll({ behavior: 'wait' });
    await page.reload();
  }
}
