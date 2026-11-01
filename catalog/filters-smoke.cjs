const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:3180/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.catalogFilters?.data?.collection.length > 0);
    assert(await page.locator('[data-filter=model]').isDisabled());
    assert.equal(await page.locator('#gift-number-filter,#filters button[type=submit]').count(), 0);
    await page.locator('[data-filter=collection]').scrollIntoViewIfNeeded();
    const beforeOpen = await page.evaluate(() => ({x:scrollX,y:scrollY,width:document.documentElement.clientWidth}));
    await page.locator('[data-filter=collection]').click();
    await page.waitForTimeout(250);
    assert.deepEqual(await page.evaluate(() => ({x:scrollX,y:scrollY,width:document.documentElement.clientWidth})), beforeOpen, 'Opening a picker must not scroll or shift the page');
    await page.getByRole('searchbox', { name: 'Поиск: Коллекция', exact: true }).fill('Toy Bear');
    await page.locator('.picker-option').filter({ hasText: 'Toy Bear' }).click();
    assert.equal(await page.locator('#collection').inputValue(), 'Toy Bear');
    await page.waitForFunction(() => window.catalogFilters.state.collection === 'Toy Bear' && !window.catalogFilters.loading);

    assert(await page.locator('[data-filter=model]').isEnabled());
    await page.locator('[data-filter=model]').scrollIntoViewIfNeeded();
    const modelY = await page.evaluate(() => scrollY);
    await page.locator('[data-filter=model]').click();
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => scrollY), modelY);
    const model = await page.locator('.picker-option input').first().inputValue();
    await page.locator('.picker-option input').first().focus();
    await page.keyboard.press('Space');
    assert.equal(await page.locator('#model').inputValue(), model);
    assert.equal(await page.locator('.catalog-picker-panel').count(), 0);
    await page.locator('[data-filter=model]').click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#model').inputValue(), model);
    await page.waitForFunction(() => document.activeElement === document.querySelector('[data-filter=model]')); 
    await page.locator('[data-filter=model]').click();
    await page.getByRole('button', { name: 'Очистить', exact: true }).click();
    assert.equal(await page.locator('#model').inputValue(), '');

    await page.locator('.catalog-sort-trigger').click();
    await page.getByRole('option', { name: 'Сначала дороже', exact: true }).click();
    assert.equal(await page.locator('#sort').inputValue(), 'price_desc');
    await page.locator('[data-filter=symbol]').click();
    await page.getByRole('searchbox', { name: 'Поиск: Узор', exact: true }).fill('no-such-symbol-zz');
    assert(await page.getByText('Ничего не найдено', { exact: true }).isVisible());
    await page.keyboard.press('Escape');
    await page.locator('#reset').click();
    await page.waitForFunction(() => !window.catalogFilters.loading && !window.catalogFilters.state.collection);
    assert.equal(await page.locator('.catalog-sort-trigger').innerText(), 'Сначала дешевле');
    assert(await page.locator('[data-filter=model]').isDisabled());
    const priceRequest = page.waitForRequest(request => request.url().includes('/api/browse?') && new URL(request.url()).searchParams.has('min_price'));
    await page.locator('#min-price').fill('5');
    const sent = await priceRequest;
    const rate = await page.evaluate(() => window.catalogCurrency.rate);
    assert(rate > 0);
    assert(Math.abs(Number(new URL(sent.url()).searchParams.get('min_price')) - 5 / rate) < 1e-8, 'Ruble bounds must be converted to TON for MRKT');
    await page.waitForFunction(() => window.catalogFilters.state.min_price === '5');
    await page.locator('#max-price').fill('3');
    await page.waitForTimeout(450);
    assert(await page.locator('#filter-error').textContent());
    assert.equal(await page.evaluate(() => window.catalogFilters.state.max_price), '');
    await page.locator('#max-price').fill('10');
    await page.waitForFunction(() => window.catalogFilters.state.max_price === '10');
    assert.equal(await page.locator('#filter-error').textContent(), '');

    await page.setViewportSize({ width: 390, height: 844 });
    if (!await page.locator('#filter-panel').evaluate(el => el.open)) await page.locator('#filter-panel summary').click();
    await page.locator('[data-filter=backdrop]').click();
    await page.waitForTimeout(250);
    const box = await page.locator('.catalog-picker-panel').boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 391 && box.y >= 0 && box.y + box.height <= 845, 'Mobile panel must fit the viewport');
    await page.keyboard.press('Escape');
    await page.locator('#theme-toggle').click();
    await page.locator('[data-filter=collection]').click();
    assert.equal(await page.locator('.catalog-picker-panel').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(248, 250, 252)');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.locator('.catalog-picker-panel').evaluate(el => getComputedStyle(el).animationName), 'none');
    assert.deepEqual(errors, []);
    console.log('Filter panels: instant selection, collection dependency, stable scroll, live price validation, keyboard, clear, sort, search, reset, mobile, light theme and reduced motion passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
