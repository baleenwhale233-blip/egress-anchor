// Target the disposable service_ui_fixture.py after the main acceptance script.
async (preview) => {
  const base = new URL(preview.url()).origin;
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw new Error('Open the disposable fixture first');
  const context = await preview.context().browser().newContext({viewport: null});
  try {
    const page = await context.newPage();
    const checks = [];
    const assert = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
    const state = async () => (await (await page.request.get(`${base}/api/state`)).json()).state;
    const update = async (id, fields) => {
      const current = await state(), record = current.services.find(s => s.id === id);
      const keys = ['id','name','template_id','extra_domains','exclude_domains','exit','strict'];
      const service = {...Object.fromEntries(keys.map(key => [key,record[key]])), ...fields};
      const response = await page.request.put(`${base}/api/services`, {data:{service,revision:record.revision,source_id:current.selected_source_id}});
      if (!response.ok()) throw new Error('fixture update failed');
    };
    const dialog = page.locator('#drawer');
    await page.goto(`${base}/#services`);
    await page.locator('#edit-claude').click();
    const original = (await state()).services.find(s => s.id === 'claude');
    await dialog.locator('[name="name"]').fill('网络失败保留草稿');
    await page.route('**/api/services', route => route.request().method()==='PUT' ? route.abort('connectionfailed') : route.continue());
    await dialog.getByRole('button',{name:'保存设置',exact:true}).click();
    await dialog.getByText('无法连接本地服务。请确认出口锚点正在运行，然后重试。').waitFor();
    assert(await dialog.locator('[name="name"]').inputValue()==='网络失败保留草稿', 'network failure preserves service draft');
    assert((await state()).services.find(s=>s.id==='claude').revision===original.revision, 'failed request leaves persisted record unchanged');
    await page.unroute('**/api/services');
    await page.keyboard.press('Escape');
    await dialog.locator('#discard-changes').click();

    await page.locator('#edit-claude').click();
    await dialog.locator('[name="name"]').fill('过期窗口草稿');
    await update('claude', {name:'另一个窗口已保存'});
    await dialog.getByRole('button',{name:'保存设置',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#form-error').textContent.includes('其他窗口'));
    assert(await dialog.locator('[name="name"]').inputValue()==='过期窗口草稿', 'stale revision keeps draft and reports conflict');
    assert((await state()).services.find(s=>s.id==='claude').name==='另一个窗口已保存', 'stale save does not overwrite newer record');
    await update('claude', {name:original.name});
    await page.keyboard.press('Escape');
    await dialog.locator('#discard-changes').click();
    await page.reload();

    let release;
    const delay = new Promise(resolve => { release=resolve; });
    const nextExit = (await state()).services.find(s=>s.id==='deepseek').exit.kind === 'inherit' ? 'direct' : 'inherit';
    await page.route('**/api/services', async route => { if(route.request().method()==='PUT') await delay; await route.continue(); });
    await page.locator(`[data-service="deepseek"] input[value="${nextExit}"]`).click();
    await page.locator('[data-service="deepseek"][aria-busy="true"]').waitFor();
    assert(await page.locator('[data-service="deepseek"] input').first().isDisabled(), 'submitting service is disabled');
    assert(!await page.locator('[data-service="claude"] input').first().isDisabled(), 'other services remain available while saving');
    release();
    await page.waitForFunction(()=>!document.querySelector('[data-service="deepseek"]').hasAttribute('aria-busy'));
    await page.unroute('**/api/services');
    await page.locator('[data-service="deepseek"] input[value="direct"]').click();
    await page.waitForFunction(()=>!document.querySelector('[data-service="deepseek"]').hasAttribute('aria-busy'));

    await page.route('**/api/state', route=>route.abort('connectionfailed'));
    await page.reload();
    await page.getByText('无法连接本地服务。请确认出口锚点正在运行，然后重试。').waitFor();
    await page.unroute('**/api/state');
    await page.locator('#reload-state').click();
    await page.locator('.service-card').first().waitFor();
    assert(await page.locator('.service-card').count()===3, 'initial load failure recovers with retry');

    await page.route('**/api/state', async route => {
      const response = await route.fetch(), result = await response.json();
      result.state.airport_choices=[];
      await route.fulfill({response,json:result});
    });
    await page.reload();
    await page.getByText('原来的机场选择已不可用，请重新选择后保存。本地订阅保留上一份有效配置。').waitFor();
    assert(await page.locator('#reference-chatgpt').inputValue()==='', 'missing reference is shown without silently selecting a replacement');
    await page.screenshot({path:'/Users/yang/Documents/egress-anchor/dev-data/ui-qa/missing-reference.png',fullPage:true,scale:'css'});
    await page.unroute('**/api/state');
    await page.reload();

    await page.getByRole('link',{name:'设置',exact:true}).click();
    const originalSource = (await state()).selected_source_id;
    await page.locator('#add-source').click();
    await dialog.locator('[name="name"]').fill('备用合成订阅');
    await dialog.locator('[name="url"]').fill('https://fixture.example.invalid/second');
    await dialog.getByRole('button',{name:'保存并读取'}).click();
    await page.waitForFunction(()=>!document.querySelector('#drawer').open);
    const added = (await state()).sources.find(s=>s.name==='备用合成订阅');
    assert(Boolean(added), 'source import works through settings form');
    await page.getByRole('button',{name:'重命名 备用合成订阅'}).click();
    await dialog.locator('[name="name"]').fill('备用订阅改名');
    await dialog.getByRole('button',{name:'保存名称'}).click();
    await page.waitForFunction(()=>!document.querySelector('#drawer').open);
    assert((await state()).sources.find(s=>s.id===added.id).name==='备用订阅改名', 'source rename preserves identity');
    if ((await state()).selected_source_id!==added.id) await page.locator(`[data-select-source="${added.id}"]`).click();
    await page.locator('#generate-subscription').click();
    await page.waitForFunction(()=>!document.querySelector('#generate-subscription').disabled);
    assert((await state()).active_source_id===added.id, 'selected source generates local subscription');
    await page.locator(`[data-select-source="${originalSource}"]`).click();
    await page.waitForFunction(()=>document.querySelector('main').textContent.includes('仍在提供上一份配置'));
    checks.push('source switch retains last good delivery until generation');
    await page.locator('#generate-subscription').click();
    await page.waitForFunction(()=>!document.querySelector('#generate-subscription').disabled);
    await page.getByRole('button',{name:'删除 备用订阅改名'}).click();
    await dialog.locator('#confirm-delete').click();
    await page.waitForFunction(()=>!document.querySelector('#drawer').open);
    assert(!(await state()).sources.some(s=>s.id===added.id), 'inactive source deletion works');

    await page.getByRole('link',{name:'服务出口',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await page.locator('#edit-deepseek').click();
    const scrollOffset = await page.evaluate(()=>parseFloat(document.body.style.getPropertyValue('--scroll-offset')));
    assert(await page.evaluate(()=>getComputedStyle(document.body).position==='fixed'), 'mobile drawer locks background scroll');
    await dialog.locator('[data-close]').first().focus();
    await page.keyboard.press('Shift+Tab');
    assert(await page.evaluate(()=>document.activeElement.getAttribute('form')==='service-form'), 'reverse Tab wraps to drawer save action');
    await page.keyboard.press('Escape');
    await page.waitForFunction(()=>!document.body.classList.contains('modal-open'));
    assert(await page.evaluate(offset=>Math.abs(window.scrollY+offset)<2,scrollOffset), 'drawer close restores background position');
    assert(await page.evaluate(()=>document.activeElement.id)==='edit-deepseek', 'drawer close restores triggering control');
    await page.setViewportSize({width:1280,height:800});
    await page.reload();
    return {passed:checks.length,checks};
  } finally {
    await context.close();
    await preview.bringToFront();
  }
}
