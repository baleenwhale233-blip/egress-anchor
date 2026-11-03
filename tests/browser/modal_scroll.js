// Start the disposable fixture with: python3 tests/browser/modal_fixture.py
// Open its printed URL, then run this file with Playwright MCP
// browser_run_code_unsafe(filename).
// Exercises real wheel input; does not change gateway data.
async (page) => {
  const read = async () => page.evaluate(() => {
    const rect = document.querySelector('.grid').getBoundingClientRect();
    return { y: scrollY, top: rect.top, left: rect.left, width: rect.width };
  });
  const sameBackground = (before, after, context) => {
    for (const key of ['top', 'left', 'width']) {
      if (Math.abs(before[key] - after[key]) > 1) {
        throw new Error(`${context}: background ${key} moved from ${before[key]} to ${after[key]}`);
      }
    }
  };
  const wheel = async (delta) => {
    await page.mouse.wheel(0, delta);
    await page.waitForTimeout(250);
  };
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForFunction(() => document.querySelector('#landing-list .landing'));
  if (!await page.evaluate(() => document.documentElement.scrollHeight > innerHeight + 600)) {
    throw new Error('This regression requires a disposable fixture with a long background page');
  }
  const cases = [];
  for (const [modal, opener, close] of [
    ['landing-modal', '#btn-add-landing', '#btn-close-modal'],
    ['source-modal', '#btn-add-source', '#btn-close-source-modal'],
  ]) {
    for (const action of ['x', 'escape', 'backdrop']) {
      await page.evaluate((selector) => {
        const top = document.querySelector(selector).getBoundingClientRect().top + scrollY;
        scrollTo(0, Math.max(0, Math.min(180, top - 60)));
      }, opener);
      const before = await read();
      await page.locator(opener).click();
      const opened = await read();
      sameBackground(before, opened, `${modal} open`);
      await page.mouse.move(5, 5);
      await wheel(500);
      const scrolled = await read();
      sameBackground(opened, scrolled, `${modal} backdrop wheel`);
      if (Math.abs(opened.y - scrolled.y) > 1) throw new Error(`${modal}: document scrolled behind dialog`);
      if (action === 'x') await page.locator(close).click();
      else if (action === 'escape') await page.keyboard.press('Escape');
      else await page.mouse.click(5, 5);
      if (await page.locator(`#${modal}`).isVisible()) throw new Error(`${modal}: ${action} did not close it`);
      const closed = await read();
      sameBackground(before, closed, `${modal} close`);
      if (Math.abs(before.y - closed.y) > 1) throw new Error(`${modal}: close lost the original scroll position`);
      await page.mouse.move(5, 5);
      await wheel(200);
      if (Math.abs((await read()).y - closed.y) < 1) throw new Error(`${modal}: page scrolling was not restored`);
      cases.push(`${modal}/${action}`);
    }
  }
  await page.setViewportSize({ width: 390, height: 420 });
  await page.locator('#btn-add-landing').click();
  const panel = page.locator('#landing-modal .sheet');
  const geometry = await panel.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return { top: rect.top, bottom: rect.bottom, viewport: innerHeight,
      clientHeight: element.clientHeight, scrollHeight: element.scrollHeight };
  });
  if (geometry.top < 0 || geometry.bottom > geometry.viewport || geometry.scrollHeight <= geometry.clientHeight) {
    throw new Error(`Long dialog is not constrained to its own scroll area: ${JSON.stringify(geometry)}`);
  }
  const background = await read();
  await page.locator('#modal-title').hover();
  await wheel(300);
  if (await panel.evaluate(element => element.scrollTop) <= 0) throw new Error('Long dialog cannot scroll internally');
  sameBackground(background, await read(), 'dialog internal wheel');
  await panel.evaluate(element => { element.scrollTop = element.scrollHeight; });
  await wheel(500);
  sameBackground(background, await read(), 'dialog lower boundary wheel');
  await page.keyboard.press('Escape');
  return { passed: true, cases, backgroundLocked: true, scrollPositionRestored: true,
    longDialogScrollsInternally: true, noScrollChaining: true, geometry };
}
