// Run against service_ui_fixture.py with Playwright MCP browser_run_code_unsafe(filename).
// Uses synthetic state only. Saves screenshots and a machine-readable result.
async (preview) => {
  const base = new URL(preview.url()).origin;
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw new Error('Open the disposable fixture first');
  const context = await preview.context().browser().newContext({viewport: null});
  try {
    const page = await context.newPage();
    const output = '/Users/yang/Documents/egress-anchor/dev-data/ui-qa';
    const checks = [];
    const assert = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
    const size = async (width, height) => {
      await page.setViewportSize({width,height});
      const measured = await page.evaluate(() => ({width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth}));
      assert(Math.abs(measured.width-width)<=1 && Math.abs(measured.height-height)<=1, `viewport ${width}x${height}`);
      assert(!measured.overflow, `no horizontal overflow ${width}x${height}`);
    };
    const dialog = page.locator('#drawer');
    const save = async () => {
      await page.getByRole('button', {name:'保存设置',exact:true}).click();
      await page.waitForFunction(() => !document.querySelector('#drawer').open);
    };
    await page.goto(base);
    await page.locator('.service-card').first().waitFor();
    await size(1280,800);
    assert(await page.locator('.service-card').count()===3, 'three builtin services');
    assert(await page.locator('.service-card input[value="inherit"]:checked').count()===3, 'fresh defaults all inherit');
    await page.screenshot({path:`${output}/initial-1280.png`,fullPage:true,scale:'css'});

    await page.locator('[data-service="claude"] input[value="isp"]').click();
    await dialog.locator('[name="reference"]').selectOption({label:'美国住宅 01'});
    await save();
    assert((await page.locator('[data-service="claude"]').innerText()).includes('待客户端更新'), 'ISP save is pending client');
    await page.locator('[data-service="chatgpt"] input[value="airport"]').click();
    assert(!await dialog.locator('[name="reference"]').innerText().then(t=>t.split('\n').includes('Default')), 'airport selector excludes direct group');
    await dialog.locator('[name="reference"]').selectOption('AI');
    await save();
    await page.locator('[data-service="deepseek"] input[value="direct"]').click();
    await page.waitForFunction(() => document.querySelector('[data-service="deepseek"] .badge').textContent==='待客户端更新');
    await page.reload();
    await page.locator('[data-service="claude"] input[value="isp"]:checked').waitFor();
    assert(await page.locator('[data-service="chatgpt"] input[value="airport"]:checked').count()===1, 'airport selection persists');
    assert(await page.locator('[data-service="deepseek"] input[value="direct"]:checked').count()===1, 'direct selection persists');
    assert(!await page.locator('main').innerText().then(t=>t.includes('已应用')||t.includes('通过 ·')), 'no invented applied or probe pass');

    await page.locator('#probe-claude').click();
    assert(await dialog.getByText('未验证',{exact:true}).count()===5, 'probe details scope stays unverified');
    await page.keyboard.press('Escape');
    assert(await page.evaluate(()=>document.activeElement.id)==='probe-claude', 'Escape restores opener focus');

    await page.locator('#edit-claude').click();
    await dialog.locator('#domain-details summary').click();
    await dialog.locator('[name="extra_domains"]').fill('https://bad.example.test/path');
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await dialog.locator('[aria-invalid="true"]').waitFor();
    assert(await dialog.locator('[name="extra_domains"]').inputValue()==='https://bad.example.test/path', 'invalid domain preserves draft');
    assert(await dialog.locator('[role="alert"]').innerText().then(t=>t.includes('不要包含')), 'field error is Chinese');
    await dialog.locator('[name="extra_domains"]').fill('');
    await save();

    await page.locator('#add-service').click();
    await dialog.locator('[name="name"]').fill('文档服务');
    await dialog.locator('[name="extra_domains"]').fill('claude.ai');
    await dialog.locator('input[value="direct"]').click();
    await page.getByRole('button',{name:'保存设置',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#form-error').textContent.includes('重叠'));
    assert(await dialog.locator('[name="name"]').inputValue()==='文档服务', 'domain conflict keeps name');
    await dialog.locator('[name="extra_domains"]').fill('docs.example.test');
    await save();
    const custom = page.locator('.service-card').filter({has:page.getByRole('heading',{name:'文档服务',exact:true})});
    const id = await custom.getAttribute('data-service');
    assert(Boolean(id?.startsWith('custom-')), 'custom service gets stable ID');
    await page.reload();
    await page.locator(`#edit-${id}`).click();
    await dialog.locator('[name="name"]').fill('文档服务 · 改名');
    await save();
    assert(await page.locator(`[data-service="${id}"] h2`).innerText()==='文档服务 · 改名', 'rename preserves stable ID');
    await page.locator(`#edit-${id}`).click();
    await dialog.locator('#delete-service').click();
    await dialog.locator('#delete-service').click();
    await page.waitForFunction(()=>!document.querySelector('#drawer').open);
    assert(await page.locator('.service-card').count()===3, 'custom deletion returns to builtins');

    await page.locator('#edit-claude').click();
    await dialog.locator('[name="name"]').fill('Claude 草稿保留');
    await dialog.locator('#quick-add-landing').click();
    await dialog.locator('[name="name"]').fill('备用住宅');
    await dialog.locator('[name="url"]').fill('socks5://fixture.example.invalid:1080');
    await page.getByRole('button',{name:'保存代理',exact:true}).click();
    await dialog.locator('#service-form').waitFor();
    assert(await dialog.locator('[name="name"]').inputValue()==='Claude 草稿保留', 'quick add landing returns with service draft');
    assert(await dialog.locator('[name="reference"] option:checked').innerText()==='备用住宅', 'quick added resource selected');
    await dialog.locator('[name="name"]').fill('Claude');
    await dialog.locator('[name="reference"]').selectOption({label:'美国住宅 01'});
    await save();

    await page.getByRole('link',{name:'落地代理',exact:true}).click();
    const used = page.locator('.resource-row').filter({has:page.getByRole('heading',{name:'美国住宅 01',exact:true})});
    assert(await used.getByRole('button',{name:'删除 美国住宅 01'}).isDisabled(), 'used landing deletion disabled');
    await page.getByRole('button',{name:'删除 备用住宅'}).click();
    await dialog.locator('#confirm-delete').click();
    await page.waitForFunction(()=>!document.querySelector('#drawer').open);
    assert(await page.locator('.resource-row').count()===1, 'unused landing deleted');

    await page.getByRole('link',{name:'设置',exact:true}).click();
    await page.locator('#subscription-url').waitFor();
    assert(await page.locator('#subscription-url').inputValue().then(v=>v.startsWith(`${base}/sub/`)), 'settings exposes generated local subscription');
    await page.locator('#generate-subscription').click();
    await page.waitForFunction(()=>!document.querySelector('#generate-subscription').disabled);
    assert(await page.locator('main').innerText().then(t=>t.includes('待客户端更新')), 'regeneration retains pending status');
    await page.getByRole('link',{name:'服务出口',exact:true}).click();
    await page.locator('#toast').waitFor({state:'hidden'});
    await size(1587,991);
    await page.screenshot({path:`${output}/services-desktop.png`,fullPage:true,scale:'css'});
    await size(1280,800);
    await page.screenshot({path:`${output}/services-1280.png`,fullPage:true,scale:'css'});
    await size(960,720);
    await page.screenshot({path:`${output}/services-960.png`,fullPage:true,scale:'css'});
    await size(390,844);
    await page.screenshot({path:`${output}/services-mobile.png`,fullPage:true,scale:'css'});
    await page.locator('#edit-claude').click();
    await dialog.locator('#domain-details summary').click();
    await page.screenshot({path:`${output}/drawer-mobile.png`,fullPage:false,scale:'css'});
    const geometry = await dialog.evaluate(el=>{const r=el.getBoundingClientRect(),footer=el.querySelector('footer').getBoundingClientRect();return{top:r.top,bottom:r.bottom,footer:footer.bottom,height:innerHeight,scroll:el.querySelector('.drawer-content').scrollHeight>el.querySelector('.drawer-content').clientHeight};});
    assert(geometry.top>=-1 && geometry.bottom<=geometry.height+1 && geometry.footer<=geometry.height+1 && geometry.scroll, 'mobile drawer bounded with reachable footer');
    for (let i=0;i<20;i++) {
      await page.keyboard.press('Tab');
      if (!await page.evaluate(()=>document.querySelector('#drawer').contains(document.activeElement))) throw new Error('dialog focus escaped');
    }
    checks.push('native dialog traps keyboard focus');
    await page.keyboard.press('Escape');
    await size(640,400);
    assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth), '200 percent equivalent reflow');
    await page.screenshot({path:`${output}/services-zoom-reflow.png`,fullPage:true,scale:'css'});
    await size(1280,800);
    return {passed:checks.length,checks};
  } finally {
    await context.close();
    await preview.bringToFront();
  }
}
