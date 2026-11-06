// Run against a fresh service_ui_fixture.py --empty instance via Playwright MCP.
async (preview) => {
  const base = new URL(preview.url()).origin;
  const context = await preview.context().browser().newContext({viewport: null});
  try {
    const page = await context.newPage();
    const checks = [];
    const assert = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
    const state = async () => (await (await page.request.get(`${base}/api/state`)).json()).state;
    if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base) || (await state()).sources.length) throw new Error('Open an empty disposable fixture first');
    const dialog = page.locator('#drawer');
    await page.goto(base);
    await page.locator('.service-card').first().waitFor();
    assert(await page.locator('.service-card input[value="inherit"]:checked').count()===3, 'empty workspace still defaults all services to inherit');
    await page.getByRole('link',{name:'落地代理',exact:true}).click();
    await page.getByRole('heading',{name:'还没有落地代理'}).waitFor();
    assert(await page.getByRole('heading',{name:'还没有落地代理'}).isVisible(), 'empty resources show useful next action');
    await page.getByRole('button',{name:'添加第一个代理'}).click();
    assert(await dialog.getByRole('heading',{name:'添加落地代理'}).isVisible(), 'empty resource action opens editor');
    await page.keyboard.press('Escape');
    await page.getByRole('link',{name:'设置',exact:true}).click();
    await page.getByText('尚未生成',{exact:true}).waitFor();
    assert(await page.getByText('尚未生成',{exact:true}).isVisible() && await page.locator('#subscription-url').count()===0, 'no invented subscription URL before generation');
    await page.screenshot({path:'/Users/yang/Documents/egress-anchor/dev-data/ui-qa/settings-empty.png',fullPage:true,scale:'css'});
    await page.getByRole('link',{name:'服务出口',exact:true}).click();
    await page.locator('[data-service="claude"] input[value="isp"]').click();
    assert(await dialog.locator('[name="reference"] option').innerText()==='请先添加落地代理', 'ISP without resources requires an explicit addition');
    await page.keyboard.press('Escape');
    await page.locator('#add-service').click();
    const longName = '超长服务名称用于检查文字换行与窗口适配'.repeat(3) + '<b>示例</b>';
    await dialog.locator('[name="name"]').fill(longName);
    await dialog.locator('[name="extra_domains"]').fill('custom.example.test');
    await dialog.locator('input[value="direct"]').click();
    await dialog.getByRole('button',{name:'保存设置',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#drawer').open);
    const custom = (await state()).services.find(s=>s.name===longName);
    assert(custom?.application==='saved', 'service can be saved before a source without claiming generated or applied');
    assert(await page.locator(`[data-service="${custom.id}"] h2`).innerText()===longName && await page.locator(`[data-service="${custom.id}"] h2 b`).count()===0, 'service name renders as text rather than HTML');
    await page.setViewportSize({width:390,height:844});
    assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth), 'long service name has no mobile horizontal overflow');
    await page.locator(`[data-service="${custom.id}"]`).screenshot({path:'/Users/yang/Documents/egress-anchor/dev-data/ui-qa/service-long-name.png',scale:'css'});
    await page.setViewportSize({width:1280,height:800});
    return {passed:checks.length,checks};
  } finally {
    await context.close();
    await preview.bringToFront();
  }
}
