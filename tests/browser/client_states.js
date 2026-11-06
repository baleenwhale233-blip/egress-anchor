// Synthetic client observations exercise UI capability states, not real clients.
async (preview) => {
  const base = new URL(preview.url()).origin;
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw new Error('Open the disposable fixture first');
  const context = await preview.context().browser().newContext({viewport:null});
  try {
    const page = await context.newPage(), checks = [];
    const assert = (value,label) => { if(!value) throw new Error(label); checks.push(label); };
    await page.setViewportSize({width:1280,height:800});
    await page.goto(`${base}/#settings`);
    await page.waitForFunction(()=>document.querySelector('[data-client-panel]')?.textContent.includes('已读取客户端信息'));
    const original = (await (await page.request.get(`${base}/api/client`)).json()).client;
    let candidate = original;
    await page.route('**/api/client', route=>route.fulfill({json:{ok:true,client:candidate}}));
    for(const [state,label] of [['not_found','未发现'],['unsupported','不支持读取'],['unavailable','读取失败'],['multiple','待选择']]) {
      candidate = {...original,state,profile:null,runtime:{state:'unconfirmed'},reason:`合成状态：${label}`,
        candidates:state==='multiple' ? [{name:'FlClash',state:'discovered',profile:{name:'测试目录一'}},{name:'FlClash',state:'discovered',profile:{name:'测试目录二'}}] : []};
      await page.locator('#refresh-client').click();
      await page.locator('[data-client-panel] .badge').getByText(label,{exact:true}).waitFor();
      assert(await page.locator('[data-client-panel]').innerText().then(t=>t.includes(`合成状态：${label}`)), `client ${state} keeps actionable reason`);
      assert(!await page.locator('#refresh-client').isDisabled(), `client ${state} can retry`);
    }
    assert(await page.locator('.client-candidates li').count()===2, 'multiple candidates are visible without silently choosing');
    await page.screenshot({path:'/Users/yang/Documents/egress-anchor/dev-data/ui-qa/client-multiple.png',fullPage:true,scale:'css'});
    candidate = {...original,runtime:{state:'reachable',mode:'global',tun:false}};
    await page.locator('#refresh-client').click();
    await page.locator('[data-client-panel]').getByText('内核可读 · 全局模式 · TUN 关闭').waitFor();
    assert(!await page.locator('[data-client-panel]').innerText().then(t=>t.includes('已应用')), 'readable core mode does not imply application');
    await page.unroute('**/api/client');
    await page.getByRole('link',{name:'服务出口',exact:true}).click();
    await page.locator('#edit-claude').click();
    const dialog=page.locator('#drawer');
    await dialog.locator('[name="name"]').fill('快速添加返回草稿');
    await dialog.locator('#quick-add-landing').click();
    await page.keyboard.press('Escape');
    await dialog.locator('#service-form').waitFor();
    assert(await dialog.locator('[name="name"]').inputValue()==='快速添加返回草稿', 'cancel quick add returns to unchanged service draft');
    await page.keyboard.press('Escape');
    await dialog.locator('#discard-changes').click();
    return {passed:checks.length,checks};
  } finally {
    await context.close();
    await preview.bringToFront();
  }
}
