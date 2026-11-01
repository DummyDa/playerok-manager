const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true});
  const context=await browser.newContext();
  await context.route('https://playerok.com/**',route=>route.fulfill({contentType:'text/html',body:'Mock payment page'}));
  const page=await context.newPage();let posts=0,queued=false;
  await page.route('**/api/status',async route=>{
    const response=await route.fetch();const data=await response.json();
    data.fx={rate:'100',fetched_at:Date.now()/1000};
    data.pricing={ready:true,ton_multiplier:'110',fixed_rub:'31.5'};
    await route.fulfill({json:data});
  });
  await page.route('**/api/checkout/**',async route=>{
    if(route.request().method()==='POST'){
      posts++;assert(route.request().postDataJSON().price_rub>0);if(posts===2)assert.equal(route.request().postDataJSON().price_rub,1022);
      return route.fulfill({json:{id:'mock-checkout'}});
    }
    if(posts===1){const gift=await page.evaluate(()=>currentGift);return route.fulfill({json:{state:'PRICE_CHANGED',price_rub:1022,updated_gift:{...gift,price_ton:'9',price_nano:9000000000,price_rub:1022,checked_at:Date.now()/1000}}});}
    if(posts===2 && !queued){queued=true;return route.fulfill({json:{state:'QUEUED',notice:'Проверяем наличие подарка перед публикацией'}});}
    return route.fulfill({json:{state:'READY',url:'https://playerok.com/products/test-checkout',expires_at:Date.now()/1000+900,price_rub:500}});
  });
  try{
    await page.goto('http://127.0.0.1:3180');
    await page.locator('.card-open').first().click();
    await page.locator('#check-gift').click();
    await page.getByText(/Цена изменилась: теперь/).waitFor();
    assert.equal(context.pages().length,1);
    const popupReady=page.waitForEvent('popup');
    await page.locator('#check-gift').click();
    await page.getByText('Проверяем наличие подарка перед публикацией').waitFor();
    assert.equal(context.pages().length,1);
    const popup=await popupReady;
    await popup.waitForURL('https://playerok.com/products/test-checkout');
    assert.equal(await popup.evaluate(()=>window.opener),null);
    const link=page.locator('#check-result a');await link.waitFor();
    assert.equal(await link.getAttribute('href'),'https://playerok.com/products/test-checkout');
    assert.equal(await page.locator('#listing-timer').isVisible(),true);
    assert.match(await page.locator('#listing-timer-value').textContent(),/^\d{2}:\d{2}$/);
    await page.bringToFront();
    await page.evaluate(()=>{renderPaidChoice();updateCurrency();showDetail(items.find(item=>item.id===currentGift.id));});
    assert.equal(await page.locator('#check-gift').textContent(),'Открыть лот на Playerok');
    assert.equal(await page.locator('#check-result a').getAttribute('href'),'https://playerok.com/products/test-checkout');
    await popup.close();
    assert.equal(posts,2);
    await page.screenshot({path:__dirname+'/data/checkout-ready.png'});
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>{currentGift.expires_at=Date.now()/1000+60;renderRegularCheckout();});
    assert.match(await page.locator('#listing-timer').getAttribute('class'),/is-urgent/);
    await page.locator('#listing-timer').scrollIntoViewIfNeeded();
    await page.screenshot({path:__dirname+'/data/checkout-timer-mobile.png'});
    await page.evaluate(()=>{currentGift.expires_at=Date.now()/1000-1;renderRegularCheckout();});
    assert.equal(await page.locator('#listing-timer-value').textContent(),'00:00');
    assert.equal(await page.locator('#check-gift').isDisabled(),true);
    assert.equal(await page.locator('#check-result a').count(),0);
    await page.route('**/api/paid-choice',route=>route.fulfill({json:{state:'EXHAUSTED',test:false,paid_rub:'800',balance_rub:'0',recipient:'buyer',purchased_ids:[]}}));
    await page.reload();
    await page.locator('#paid-summary').filter({hasText:'Баланс использован'}).waitFor();
    await page.locator('.card-open').first().click();
    assert.match(await page.locator('#check-gift').textContent(),/^Купить за /);
    await page.evaluate(()=>{window.open=()=>null;});
    await page.locator('#check-gift').click();
    await page.locator('#check-result a').waitFor();
    assert.equal(posts,3);
    assert.equal(await page.locator('#recipient-dialog').isVisible(),false);
    await page.evaluate(()=>renderPaidChoice());
    assert.equal(await page.locator('#check-gift').textContent(),'Открыть лот на Playerok');
    console.log('Automatic payment tab, null opener, persistent link after refresh/reopen, popup-blocker fallback and exhausted-balance checkout passed; all writes mocked.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
