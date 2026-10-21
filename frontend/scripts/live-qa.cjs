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
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:3100/');
    await page.getByRole('heading', { name: 'Обзор', exact: true }).waitFor();
    await page.screenshot({ path: path.join(qa, 'live-dashboard.png'), fullPage: true });
    await page.goto('http://127.0.0.1:3100/lots');
    await page.getByRole('table').waitFor();
    await page.screenshot({ path: path.join(qa, 'live-lots.png'), fullPage: true });
    await page.goto('http://127.0.0.1:3100/settings');
    await page.getByRole('heading', { name: '1. Playerok', exact: true }).waitFor();
    await page.screenshot({ path: path.join(qa, 'live-settings.png'), fullPage: true });
    if (errors.length) throw new Error(errors.join('; '));
    console.log(JSON.stringify({ liveDashboard: true, liveLots: true, connectionSettings: true, readOnly: true, browserErrors: 0 }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
