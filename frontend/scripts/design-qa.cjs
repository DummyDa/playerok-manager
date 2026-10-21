// Read-only visual verification against the local application.
// Prepare the temporary session with scripts/qa_session.py and clean it up afterwards.
const { chromium } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const qa = path.resolve(__dirname, '../../data/qa');
(async () => {
  const session = JSON.parse(fs.readFileSync(path.join(qa, 'session.json'), 'utf8'));
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addCookies([{ name: 'manager_session', value: session.token, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Strict' }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    for (const section of ['', 'lots', 'orders', 'gifts', 'pricing', 'fees', 'competitors', 'promotions', 'finance', 'history', 'settings']) {
      await page.goto('http://127.0.0.1:3100/' + section);
      await page.locator('h1').waitFor();
      await page.locator('.loading').waitFor({ state: 'hidden' });
      await page.evaluate(() => document.fonts.ready);
      if (['', 'lots', 'gifts'].includes(section)) {
        await page.screenshot({ path: path.join(qa, `design-live-${section || 'overview'}.png`), fullPage: true });
      }
    }
    await page.setViewportSize({ width: 375, height: 850 });
    await page.goto('http://127.0.0.1:3100/');
    await page.locator('h1').waitFor();
    if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Mobile horizontal overflow');
    await page.screenshot({ path: path.join(qa, 'design-live-mobile.png'), fullPage: true });
    if (errors.length) throw new Error(errors.join('; '));
    console.log('11 live sections checked; no browser errors; mobile viewport fits.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
