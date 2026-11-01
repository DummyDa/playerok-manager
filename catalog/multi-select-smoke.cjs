const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch();
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:3180/',{waitUntil:'domcontentloaded'});
  await page.waitForSelector('.gift-card');
  const fixture=await page.evaluate(()=>items.slice(0,3));
  await page.route('**/api/browse?**',async route=>{
   await new Promise(resolve=>setTimeout(resolve,900));
   await route.fulfill({json:{items:fixture,total:3,next_offset:null,snapshot:null,pending:false}});
  });
  await page.locator('[data-filter=collection]').click();
  const search=page.getByRole('searchbox',{name:'Поиск: Коллекция',exact:true});
  await search.fill('Toy Bear');
  await page.locator('.picker-option').filter({hasText:'Toy Bear'}).click();
  assert.equal(await page.locator('#grid .gift-card').count(),0);
  assert.equal(await page.locator('.loading-card').count(),12);
  assert.equal(await page.locator('.catalog-picker-panel[data-state=open]').count(),1);
  await search.fill('Chill Flame');
  await page.locator('.picker-option').filter({hasText:'Chill Flame'}).click();
  await page.waitForFunction(()=>{try{return JSON.parse(window.catalogFilters.state.collection).length===2}catch{return false}});
  assert.equal(await page.locator('.applied-filter').count(),2);
  await page.keyboard.press('Escape');await page.waitForTimeout(200);
  for(const field of ['model','backdrop','symbol']) {
   await page.locator(`[data-filter=${field}]`).click();
   await page.waitForSelector('.picker-option');
   await page.locator('.picker-option').nth(0).click();
   await page.locator('.picker-option').nth(1).click();
   await page.waitForFunction(field=>{try{return JSON.parse(window.catalogFilters.state[field]).length===2}catch{return false}},field);
   await page.keyboard.press('Escape');await page.waitForTimeout(200);
  }
  assert.equal(await page.locator('.applied-filter').count(),8);
  await page.waitForFunction(()=>document.querySelectorAll('#grid .gift-card').length===3);
  await page.locator('#catalog').scrollIntoViewIfNeeded();
  await page.screenshot({path:'data/multiselect-chips.png'});
  await page.setViewportSize({width:375,height:812});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('.applied-filter').last().click();
  assert.equal(await page.locator('.applied-filter').count(),7);
  await page.locator('.applied-reset').click();
  await page.waitForFunction(()=>!window.catalogFilters.state.collection&&!window.catalogFilters.state.model&&!window.catalogFilters.state.backdrop);
  assert.equal(await page.locator('.applied-filter').count(),0);
  assert.deepEqual(errors,[]);
  console.log('Multiple collections/models/backdrops/symbols, loading skeletons, removable chips, reset and mobile passed.');
 } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
