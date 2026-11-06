// Run on the visible service preview after viewport-based acceptance.
// Resize the native Chromium window; never change its emulated viewport here.
async (page) => {
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/(#services)?$/.test(page.url())) throw new Error('Open the service fixture home first');
  const session = await page.context().newCDPSession(page);
  const {windowId, bounds} = await session.send('Browser.getWindowForTarget');
  const samples = [];
  try {
    for (const width of [1400, 1200, 1000, 800, 1400]) {
      await session.send('Browser.setWindowBounds', {windowId, bounds: {width, height: 800}});
      await page.waitForTimeout(200);
      const sample = await page.evaluate(() => {
        const card = document.querySelector('.service-card').getBoundingClientRect();
        return {window: outerWidth, viewport: innerWidth, client: document.documentElement.clientWidth,
          scroll: document.documentElement.scrollWidth, card: card.width, right: card.right};
      });
      const previous = samples.at(-1);
      if (previous && Math.abs(sample.window - previous.window) > 20 &&
          Math.sign(sample.viewport - previous.viewport) !== Math.sign(sample.window - previous.window)) {
        throw new Error(`Page width is locked: window ${previous.window}→${sample.window}, viewport ${previous.viewport}→${sample.viewport}`);
      }
      if (sample.scroll > sample.client + 1 || sample.right > sample.client + 1) throw new Error(`Horizontal overflow at ${sample.window}px`);
      samples.push(sample);
    }
    await page.locator('#edit-claude').click();
    await page.locator('#drawer[open]').waitFor();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#drawer').open);
    if (await page.evaluate(() => document.activeElement.id) !== 'edit-claude') throw new Error('Editor did not restore focus');
    return {passed: true, nativeWindowFollows: true, editorWorks: true, samples};
  } finally {
    await session.send('Browser.setWindowBounds', {windowId, bounds});
    await session.detach();
  }
}
