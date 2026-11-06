// First C batch acceptance. Run on the synthetic fixture after service_ui.js.
async (preview) => {
  const base = new URL(preview.url()).origin;
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw new Error('Open the disposable fixture first');
  const context = await preview.context().browser().newContext({viewport: null});
  try {
    const page = await context.newPage(), errors = [], checks = [];
    page.on('pageerror', error => errors.push(error.message));
    const assert = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
    const dialog = page.locator('#drawer');
    const output = '/Users/yang/Documents/egress-anchor/dev-data/ui-qa';
    await page.setViewportSize({width:1280,height:800});
    await page.goto(base);
    await page.locator('[data-client-summary]').getByText('FlClash · 已发现').waitFor();
    assert(await page.locator('[data-client-summary]').innerText().then(t => t.includes('合成客户端订阅') && t.includes('只读查看')), 'client discovery shows actual fixture profile and read-only access');
    assert(!await page.locator('main').innerText().then(t => t.includes('已应用') || t.includes('已验证')), 'discovery does not imply applied or verified service');

    await page.locator('#edit-claude').click();
    assert(await dialog.locator('[name="protection-preset"]').count() === 3, 'three protection presets');
    assert(await dialog.locator('[name="protection-preset"][value="off"]').isChecked(), 'protection defaults off');
    assert(await dialog.locator('[name="protection-preset"][value="recommended"]').isDisabled() && await dialog.locator('[name="protection-preset"][value="custom"]').isDisabled(), 'unsupported presets cannot be enabled');
    assert(await dialog.locator('details[open]').count() === 0, 'details start collapsed for builtin services');
    const originalName = await dialog.locator('[name="name"]').inputValue();
    await dialog.locator('[name="name"]').fill(`${originalName} 修改`);
    await dialog.getByText('1 项修改待保存', {exact:true}).waitFor();
    assert(await dialog.locator('.runtime-summary').innerText().then(t=>t.includes('尚未确认')), 'draft is separate from actual runtime');
    await page.screenshot({path:`${output}/settings-drawer-1280.png`,scale:'css'});
    await page.keyboard.press('Escape');
    assert(await dialog.locator('#discard-notice').isVisible(), 'Escape guards unsaved changes');
    assert(await page.evaluate(()=>document.activeElement.id)==='keep-editing', 'discard prompt focuses keep editing');
    await dialog.locator('#keep-editing').click();
    assert(await dialog.locator('[name="name"]').inputValue()===`${originalName} 修改`, 'keep editing retains draft');
    await dialog.locator('#protection-details summary').click();
    assert(await dialog.locator('.protection-item').count()===4, 'UDP DNS IPv6 and WebRTC have separate details');
    const udp = dialog.locator('.protection-item').filter({has: page.getByRole('heading',{name:'UDP',exact:true})});
    assert(await udp.innerText().then(t=>t.includes('拒绝 UDP') && t.includes('基础约束')), 'ISP retains mandatory UDP restriction with protection off');
    await dialog.locator('[name="editor-exit"][value="direct"]').click();
    assert(await udp.locator('dd').last().innerText()==='沿用订阅', 'draft exit change updates protection values');
    await dialog.locator('[name="editor-exit"][value="isp"]').click();
    assert(await dialog.locator('.protection-item .scope-tag').allTextContents().then(v=>v.join('|')==='本服务|整个客户端|整个客户端|当前浏览器'), 'protection detail exposes real scope');
    await dialog.locator('#verification-details summary').click();
    assert(await dialog.locator('#verification-details .probe-item').count()===5, 'service verification separated into five evidence items');
    await dialog.locator('#probe-settings-details summary').click();
    assert(await dialog.locator('#probe-settings-details').innerText().then(t=>t.includes('5 分钟') && t.includes('8 秒') && t.includes('暂不生效')), 'advanced recommended settings do not claim an active scheduler');
    await dialog.locator('#protection-details').scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/settings-protection-1280.png`,scale:'css'});
    await dialog.locator('[data-close]').first().click();
    await dialog.locator('#discard-changes').click();
    await page.waitForFunction(()=>!document.querySelector('#drawer').open);
    await page.locator('#edit-claude').click();
    assert(await dialog.locator('[name="name"]').inputValue()===originalName, 'discard leaves saved service unchanged');
    await page.keyboard.press('Escape');

    await page.getByRole('link',{name:'设置',exact:true}).click();
    await page.locator('[data-client-panel]').getByText('合成客户端订阅',{exact:true}).waitFor();
    await page.screenshot({path:`${output}/client-readonly-1280.png`,fullPage:true,scale:'css'});
    let release;
    const pending = new Promise(resolve=>{release=resolve;});
    await page.route('**/api/client', async route=>{await pending; await route.continue();});
    await page.locator('#refresh-client').click();
    assert(await page.locator('#refresh-client').isDisabled(), 'discovery disables only its own refresh action');
    await page.getByRole('link',{name:'服务出口',exact:true}).click();
    await page.locator('#edit-claude').click();
    await dialog.locator('[name="name"]').fill('检查期间的草稿');
    release();
    await page.waitForFunction(()=>document.querySelector('[data-client-summary]').textContent.includes('已发现'));
    assert(await dialog.locator('[name="name"]').inputValue()==='检查期间的草稿', 'background discovery preserves open form and input');
    await page.unroute('**/api/client');

    for (const [width,height] of [[960,720],[390,844],[640,400]]) {
      await page.setViewportSize({width,height});
      const geometry = await dialog.evaluate(el=>{
        const r=el.getBoundingClientRect(),footer=el.querySelector('footer').getBoundingClientRect();
        return {width:innerWidth,height:innerHeight,left:r.left,right:r.right,top:r.top,bottom:r.bottom,
          footer:footer.bottom,overflow:document.documentElement.scrollWidth>innerWidth,
          internal:el.querySelector('.drawer-content').scrollHeight>el.querySelector('.drawer-content').clientHeight};
      });
      assert(geometry.width===width && geometry.height===height && !geometry.overflow && geometry.left>=-1 && geometry.right<=width+1 && geometry.top>=-1 && geometry.bottom<=height+1 && geometry.footer<=height+1 && geometry.internal, `drawer bounds, inner scroll and visible footer ${width}x${height}`);
      await dialog.locator('.drawer-content').evaluate(el=>{el.scrollTop=0;});
      await page.screenshot({path:`${output}/settings-drawer-${width}.png`,scale:'css'});
    }
    await page.keyboard.press('Escape');
    await dialog.locator('#discard-changes').click();
    await page.setViewportSize({width:1280,height:800});
    await page.getByRole('link',{name:'设置',exact:true}).click();
    await page.route('**/api/client', route=>route.abort('connectionfailed'));
    await page.locator('#refresh-client').click();
    await page.locator('#client-error').getByText('无法连接本地服务。请确认出口锚点正在运行，然后重试。').waitFor();
    assert(!await page.locator('#refresh-client').isDisabled(), 'failed discovery keeps retry available');
    await page.unroute('**/api/client');
    await page.locator('#refresh-client').click();
    await page.waitForFunction(()=>!document.querySelector('#refresh-client').disabled && !document.querySelector('#client-error').textContent);
    assert(errors.length===0, 'no JavaScript runtime errors');
    return {passed:checks.length,checks};
  } finally {
    await context.close();
    await preview.bringToFront();
  }
}
