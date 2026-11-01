// Browser-only fixtures. No real credit, listing, payment or NFT is created.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true});
  const errors=[];let ordinaryWrites=0,testPaymentWrites=0;
  try{
    for(const amount of [99999,1]){
      const context=await browser.newContext({viewport:amount===1?{width:390,height:844}:{width:1440,height:1000}});
      const page=await context.newPage();let active=false,credit={test:false,state:'AWAITING_SELECTION',paid_rub:String(amount),balance_rub:String(amount),reserved_rub:'0',recipient:'catalog_test',purchased_ids:[]};
      page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/api/status',async route=>{const response=await route.fetch();const data=await response.json();data.fx={rate:'100',fetched_at:Date.now()/1000};data.pricing={ready:true,ton_multiplier:'110',fixed_rub:'31.5'};return route.fulfill({json:data});});
      await page.route('**/api/checkout/**',route=>{ordinaryWrites++;return route.abort();});
      await page.route('https://playerok.com/**',route=>route.fulfill({contentType:'text/html',body:'Mock payment page'}));
      await page.route('**/api/paid-choice**',async route=>{
        const request=route.request(),url=new URL(request.url());
        if(url.pathname.endsWith('/test-topup')){testPaymentWrites++;return route.fulfill({status:410,json:{detail:'Removed'}});}
        if(url.pathname.endsWith('/activate')){active=true;return route.fulfill({json:credit});}
        if(request.method()==='DELETE'){active=false;return route.fulfill({json:{ok:true}});}
        if(url.pathname.includes('/select/')){
          const body=request.postDataJSON(),gift=await page.evaluate(()=>currentGift);
          const available=Number(credit.balance_rub),due=body.price_rub-available<=20?0:Math.max(90,body.price_rub-available);
          credit={...credit,state:due?'PREPARING':'EXECUTING',selected:gift,selected_price:available+due,recipient:body.recipient,balance_rub:"0",reserved_rub:String(available),topup_rub:String(due)};
          return route.fulfill({json:credit});
        }
        return route.fulfill(active?{json:credit}:{status:401,json:{detail:'Введите код'}});
      });
      const refresh=()=>page.evaluate(async()=>{paidChoice=realCredit(await api('/api/paid-choice'));renderPaidChoice();});
      const delivered=async()=>{credit={...credit,state:'DELETED',closed_after_delivery:true,balance_rub:'0',reserved_rub:'0',purchased_ids:[...credit.purchased_ids,credit.selected.id],gift_link:'https://t.me/nft/'+credit.selected.slug};await refresh();};
      await page.goto('http://127.0.0.1:3180/?collection=Liberty+Figure#code=ABCD-EFGH');
      await page.locator('#paid-summary').filter({hasText:'Оплата применена'}).waitFor();
      assert(!(await page.evaluate(()=>location.hash)).includes('code='));
      await page.getByRole('button',{name:'Выйти из заказа',exact:true}).click();
      await page.reload();await page.locator('#payment-code').waitFor();
      await page.locator('#payment-code').fill('ABCD-EFGH');await page.getByRole('button',{name:'Применить',exact:true}).click();
      await page.locator('#paid-summary').filter({hasText:'Оплата применена'}).waitFor();
      assert.equal(await page.locator('.gift-price').first().textContent(),'Получить');
      await page.locator('.card-open').first().click();await page.locator('#check-gift').click();
      const confirmation=page.getByRole('dialog',{name:'Подтвердите получателя'});await confirmation.waitFor();
      assert.equal(credit.state,'AWAITING_SELECTION');
      if(amount===99999){
        await confirmation.getByRole('button',{name:'Сменить юз',exact:true}).click();await confirmation.getByLabel('Telegram-юзернейм',{exact:true}).fill('@new_recipient');
      }
      await confirmation.screenshot({path:path.join(__dirname,amount===1?'data/recipient-confirm-mobile.png':'data/recipient-confirm-desktop.png')});
      await confirmation.getByRole('button',{name:'Да',exact:true}).click();
      if(amount===99999){
        await page.getByRole('button',{name:'Проверяем и выдаём подарок…',exact:true}).waitFor();
        assert.equal(credit.recipient,'new_recipient');assert.deepEqual(credit.purchased_ids,[]);
        await delivered();await page.getByRole('button',{name:'Подарок получен',exact:true}).waitFor();
        assert.equal(Number(credit.balance_rub),0);assert.equal(credit.purchased_ids.length,1);
        await page.locator('#close-dialog').click();
        await page.getByRole('button',{name:'Выйти из заказа',exact:true}).click();
        assert((await page.locator('.gift-price').first().textContent()).includes('₽'));
      }else{
        await page.getByRole('button',{name:'Создаём лот доплаты…',exact:true}).waitFor();
        credit={...credit,state:'AWAITING_TOPUP',payment_url:'https://playerok.com/products/mock-topup',expires_at:Date.now()/1000+900};await refresh();
        const popupPromise=page.waitForEvent('popup');await page.getByRole('button',{name:/на Playerok$/}).click();
        const popup=await popupPromise;await popup.waitForLoadState();assert.equal(popup.url(),credit.payment_url);await popup.close();
        assert.equal(credit.state,'AWAITING_TOPUP');assert.deepEqual(credit.purchased_ids,[]);
        credit={...credit,state:'EXECUTING',paid_rub:String(credit.selected_price),reserved_rub:String(credit.selected_price)};await refresh();
        await page.getByRole('button',{name:'Проверяем и выдаём подарок…',exact:true}).waitFor();
        await delivered();await page.getByRole('button',{name:'Подарок получен',exact:true}).waitFor();
      }
      await context.close();
    }
    assert.equal(ordinaryWrites,0);assert.equal(testPaymentWrites,0);assert.deepEqual(errors,[]);
    console.log('Real-flow UI passed: recipient, reserved funds, real payment link, pending delivery, confirmed balance; every write was mocked.');
  }finally{await browser.close();}
})().catch(error=>{console.error(String(error.message).replace(/code=[A-Z-]+/g,'code=[redacted]'));process.exit(1);});
