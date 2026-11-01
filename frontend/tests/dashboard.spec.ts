import { test, expect, type Page } from "@playwright/test";
const lotId = "own-lot";
const settings = { emergency_stop: false, repricing_enabled: false, purchases_enabled: false, transfers_enabled: false, promotions_enabled: false, notifications_enabled: false, delivery_tested: false,
  stars_rub: null, transfer_stars_reserve: 25, purchase_limit_rub: "0", daily_purchase_limit_rub: "0", daily_promotion_limit_rub: "0", quote_interval: 3600, competitor_interval: 900, quote_max_age: 7200,
  description_template: "Описание магазина", playerok_username: "ExampleStore", playerok_account_id: "seller", owner_chat_id: 1, orders_baselined: false };
const pair = { id: "pair", collection: "Liberty Figure", model: "Obsidian" };
const lot = { id: lotId, title: "Liberty Figure — Obsidian", description: "Пример объявления", pair_id: "pair", pair, status: "APPROVED", price: 800, raw_price: 1500, fee_profile_id: "default", url: "https://playerok.com/products/example", image: null };
const connections = { items: [{ id: "playerok", configured: true, status: "CONNECTED", last_success: Date.now() / 1000 }, { id: "mrkt", configured: true, status: "CONNECTED", mrkt_ton: "0", stars: 138 }, { id: "telegram", configured: true, status: "CONNECTED" }], worker: { status: "IDLE", heartbeat: Date.now() / 1000 } };
const preview = { id: "preview", state: "PREVIEW", action: "promotion", payload: { lot_id: lotId }, expected: { cost_rub: "25", balance: 100 } };
const events = [{ id: "one", title: "Лоты синхронизированы", detail: "46 объявлений обновлены", action: "sync", level: "INFO", updated_at: Date.now() / 1000 }];

test("manual gift delivery requires explicit recipient confirmation",async({page})=>{
  await mock(page);
  let confirmations=0;
  const delivery={id:"manual-child",gift_slug:"SnoopDogg-1",recipient:"buyer",sale_rub:"500",stage:"AWAITING_MANUAL_DELIVERY",manual_delivery_allowed:true};
  const group={id:"root",catalog_group:true,title:"Заказ каталога",stage:delivery.stage,paid_rub:"500",balance_rub:"0",delivery_count:0,deliveries:[delivery],payments:[]};
  await page.route("**/api/v1/orders",route=>route.fulfill({json:{items:[group],operations:[]}}));
  await page.route("**/api/v1/orders/manual-child/manual-delivered",route=>{confirmations++;return route.fulfill({json:{task_id:"manual-task"}});});
  await page.route("**/api/v1/tasks/manual-task",route=>route.fulfill({json:{state:"COMPLETE",result:{operation_id:"manual-mark"}}}));
  await page.route("**/api/v1/operations/manual-mark",route=>route.fulfill({json:{state:"COMPLETE"}}));
  await page.goto("/orders");
  await page.getByRole("button",{name:"Подробнее",exact:true}).click();
  await page.getByRole("button",{name:"Выдан",exact:true}).click();
  const confirm=page.getByRole("dialog",{name:"Подтвердить ручную выдачу",exact:true});
  await expect(confirm.getByText("SnoopDogg-1",{exact:true})).toBeVisible();
  await expect(confirm.getByText("@buyer",{exact:true})).toBeVisible();
  expect(confirmations).toBe(0);
  await confirm.getByRole("button",{name:"Да, подарок передан",exact:true}).click();
  await expect(confirm).toHaveCount(0);
  expect(confirmations).toBe(1);
});

test("discontinued listing can be republished from other lots after confirmation",async({page})=>{
  await mock(page);
  const op={id:"relist",action:"relist",state:"PREVIEW",payload:{lot_id:lotId,title:lot.title,price:800},expected:{cost_rub:"0"}};
  await page.route("**/api/v1/lots",route=>route.fulfill({json:{items:[{...lot,status:"DISCONTINUED"}]}}));
  await page.route(`**/api/v1/lots/${lotId}/relist-preview`,route=>route.fulfill({json:op}));
  await page.route("**/api/v1/operations/execute",route=>route.fulfill({json:{...op,state:"QUEUED"}}));
  await page.route("**/api/v1/operations/relist",route=>route.fulfill({json:{...op,state:"COMPLETE"}}));
  await page.goto("/lots");
  await page.getByRole("button",{name:"Остальные",exact:true}).click();
  await page.getByRole("button",{name:"Выставить повторно",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Выставить лот повторно"});
  await expect(dialog.getByText(/Бесплатная публикация без премиума/)).toBeVisible();
  await dialog.getByRole("button",{name:"Подтвердить",exact:true}).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Операция выполнена",{exact:true})).toBeVisible();
});

test("relisting can quote premium before explicit paid confirmation",async({page})=>{
  await mock(page);
  const free={id:"free-relist",action:"relist",state:"PREVIEW",payload:{lot_id:lotId,title:lot.title,price:800,premium:false},expected:{cost_rub:"0"}};
  const premium={...free,id:"paid-relist",payload:{...free.payload,premium:true},expected:{cost_rub:"25",balance:100,status_id:"premium"}};
  let executed="";
  await page.route("**/api/v1/settings",route=>route.fulfill({json:{...settings,promotions_enabled:true}}));
  await page.route("**/api/v1/lots",route=>route.fulfill({json:{items:[{...lot,status:"SOLD"}]}}));
  await page.route(`**/api/v1/lots/${lotId}/relist-preview`,route=>route.fulfill({json:route.request().postDataJSON()?.premium?{task_id:"quote-premium"}:free}));
  await page.route("**/api/v1/tasks/quote-premium",route=>route.fulfill({json:{state:"COMPLETE",result:premium}}));
  await page.route("**/api/v1/operations/execute",route=>{executed=route.request().postDataJSON().operation_id;return route.fulfill({json:{state:"QUEUED"}});});
  await page.route("**/api/v1/operations/paid-relist",route=>route.fulfill({json:{...premium,state:"COMPLETE"}}));
  await page.goto("/lots");
  await page.getByRole("button",{name:"Остальные",exact:true}).click();
  await page.getByRole("button",{name:"Выставить повторно",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Выставить лот повторно"});
  await dialog.getByRole("checkbox",{name:/Выставить с премиумом/}).check();
  await expect(dialog.getByText(/Публикация с премиумом: 25/)).toBeVisible();
  expect(executed).toBe("");
  await dialog.getByRole("button",{name:"Подтвердить",exact:true}).click();
  await expect(dialog).toHaveCount(0);
  expect(executed).toBe("paid-relist");
});

test("profit calculator is a separate owner tab and submits saved fee profile",async({page})=>{
  await mock(page);
  let input:any;
  await page.route("**/api/v1/calculator",route=>{input=route.request().postDataJSON();return route.fulfill({json:{profit_rub:"61.71",sale_rub:"635",purchase_rub:"401",fees_rub:"127.79",transfer_rub:"31.5",promotion_rub:"13",break_even_rub:"562.06",margin_percent:"9.718",ton_rub:"100",stars_rub:"1.26",transfer_stars:25,fees:[{name:"Playerok",amount_rub:"63.5"}]}});});
  await page.goto("/calculator");
  await expect(page.getByRole("heading",{name:"Калькулятор",exact:true})).toBeVisible();
  await page.getByLabel("Цена продажи, ₽",{exact:true}).fill("635");
  await page.getByLabel("Стоимость закупки",{exact:true}).fill("4.01");
  await page.getByRole("checkbox",{name:/Учитывать премиум/}).check();
  await page.getByLabel("Стоимость одного поднятия, ₽").fill("13");
  await page.getByRole("button",{name:"Рассчитать прибыль"}).click();
  await expect(page.getByText("61,71 ₽",{exact:true})).toBeVisible();
  expect(input).toMatchObject({fee_profile_id:"default",purchase:"4.01",sale_rub:"635",premium_enabled:true,premium_rub:"13"});
});

test("loss sales require explicit saved owner setting",async({page})=>{
  const writes=await mock(page);
  await page.goto("/settings");
  const toggle=page.getByRole("checkbox",{name:/Разрешать выдачу в убыток/});
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await page.getByRole("button",{name:"Сохранить настройки",exact:true}).click();
  await expect.poll(()=>writes.some(w=>w.path==="/settings"&&w.body.allow_loss_sales===true)).toBe(true);
});

test("interrupted listing offers existing draft publication",async({page})=>{
  await mock(page);
  await page.route("**/api/v1/lots/recoveries",route=>route.fulfill({json:{items:[{id:"interrupted",title:"Новый подарок",error:"createItem: timeout"}]}}));
  await page.route("**/api/v1/lots/recoveries/interrupted/preview",route=>route.fulfill({json:{task_id:"resume-task"}}));
  await page.route("**/api/v1/tasks/resume-task",route=>route.fulfill({json:{state:"COMPLETE",result:{id:"resume",action:"create",state:"PREVIEW",payload:{fields:{title:"Новый подарок",resume_item_id:"draft",premium:false}},expected:{}}}}));
  await page.goto("/lots");
  await page.getByRole("button",{name:"Продолжить публикацию",exact:true}).click();
  await expect(page.getByRole("dialog",{name:"Подтвердить изменения"})).toBeVisible();
  await expect(page.getByText("Обычная публикация без премиума.",{exact:true})).toBeVisible();
});

test("listing change shows Playerok refusal after queue",async({page})=>{
  await mock(page);
  const op={id:"update-price",state:"PREVIEW",action:"update",payload:{lot_id:lotId,changes:{price:540}},expected:{}};
  await page.route(`**/api/v1/lots/${lotId}`,route=>route.fulfill({json:lot}));
  await page.route(`**/api/v1/lots/${lotId}/edit-preview`,route=>route.fulfill({json:op}));
  await page.route("**/api/v1/operations/execute",route=>route.fulfill({json:{...op,state:"QUEUED"}}));
  await page.route("**/api/v1/operations/update-price",route=>route.fulfill({json:{...op,state:"UNKNOWN",error:"Минимальная скидка — 5%"}}));
  await page.goto("/lots");
  await page.getByRole("button",{name:`Редактировать ${lot.title}`,exact:true}).click();
  const edit=page.getByRole("dialog",{name:"Редактировать объявление"});
  await edit.getByLabel("Цена, ₽",{exact:true}).fill("540");
  await edit.getByRole("button",{name:"Проверить изменения"}).click();
  await page.getByRole("button",{name:"Подтвердить",exact:true}).click();
  await expect(page.getByText("Минимальная скидка — 5%",{exact:true})).toBeVisible();
  await expect(page.getByText("Операция выполнена",{exact:true})).toHaveCount(0);
});

test("catalog order combines payments balance and deliveries in one live card",async({page})=>{
  await mock(page);
  const group:any={id:"root",catalog_group:true,title:"Заказ каталога · Snoop Dogg",stage:"AWAITING_SELECTION",sale_rub:"1200",paid_rub:"1200",manual_credit_rub:"0",adjustment_rub:"0",balance_rub:"100",reserved_rub:"0",refunded_rub:"0",recipient:"buyer",delivery_count:2,profit_rub:"300",totals:{revenue:"1100",purchase:"800",transfer:"0",fees:"0",promotion:"0"},payments:[{id:"original",kind:"ORIGINAL",accepted:true,status:"SENT",amount_rub:"1000"},{id:"extra",accepted:true,status:"PAID",amount_rub:"200"}],deliveries:[{id:"child",gift_slug:"SnoopDogg-1",recipient:"buyer",sale_rub:"700",stage:"COMPLETE",retry_allowed:false}]};
  await page.route("**/api/v1/orders",route=>route.fulfill({json:{items:[group],operations:[]}}));
  await page.goto("/orders");
  await page.getByRole("button",{name:"Подробнее",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Заказ каталога",exact:true});
  await expect(dialog.getByText("Исходная оплата",{exact:true})).toBeVisible();
  await expect(dialog.getByText("Доплата",{exact:true})).toBeVisible();
  await expect(dialog.getByText("SnoopDogg-1",{exact:true})).toBeVisible();
  await expect(dialog.getByText("Прибыль учтённых выдач",{exact:true})).toBeVisible();
  await expect(dialog.getByLabel("Комментарий ручной обработки")).toHaveCount(0);
  group.stage="COMPLETE";
  await expect(dialog.locator(".order-title").getByText("Завершён",{exact:true})).toBeVisible({timeout:10000});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:"../data/qa/catalog-order.png",fullPage:true});
});

test("new listing keep in sale is off by default",async({page})=>{
  await mock(page);await page.goto("/lots");
  await page.getByRole("button",{name:"Новый лот",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Новое объявление"});
  const toggle=dialog.getByRole("checkbox",{name:/Оставлять в продаже/});
  await expect(toggle).not.toBeChecked();await toggle.check();await expect(toggle).toBeChecked();
  await dialog.locator("header").getByRole("button",{name:"Закрыть",exact:true}).click();
  await page.getByRole("button",{name:"Новый лот",exact:true}).click();
  await expect(toggle).not.toBeChecked();
});

test("catalog profit settings save without premium",async({page})=>{
  const writes=await mock(page);
  await page.goto("/catalog");
  await page.getByLabel("Прибыль каталога",{exact:true}).fill("11");
  await page.getByLabel("Единица прибыли каталога",{exact:true}).selectOption("percent");
  await expect(page.getByLabel("Премиум на продажу в каталоге, ₽",{exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"Сохранить настройки каталога",exact:true}).click();
  await expect.poll(()=>writes.filter(w=>w.path==="/settings").length).toBe(1);
  expect(writes.find(w=>w.path==="/settings")?.body).toMatchObject({catalog_profit_value:"11",catalog_profit_type:"percent"});
});

test("catalog has a separate navigation tab and saves its description",async({page})=>{
  const writes=await mock(page);
  await page.goto("/settings");
  await expect(page.getByRole("heading",{name:"Коды каталога",exact:true})).toHaveCount(0);
  await expect(page.getByLabel("Прибыль каталога",{exact:true})).toHaveCount(0);
  await page.getByRole("link",{name:"Каталог",exact:true}).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.getByRole("heading",{name:"Каталог",level:1,exact:true})).toBeVisible();
  await expect(page.getByRole("heading",{name:"Коды каталога",exact:true})).toBeVisible();
  await page.getByLabel("Шаблон описания новых лотов",{exact:true}).fill("Подарок {collection} #{number}");
  await page.getByRole("button",{name:"Сохранить настройки каталога",exact:true}).click();
  await expect.poll(()=>writes.filter(w=>w.path==="/settings").length).toBe(1);
  expect(writes.find(w=>w.path==="/settings")?.body).toEqual({description_template:"Подарок {collection} #{number}"});
});

test("catalog payment messages and local catalog address can be edited",async({page})=>{
  const writes=await mock(page);await page.goto("/catalog");
  await page.getByLabel("Адрес каталога для покупателей",{exact:true}).fill("http://127.0.0.1:3180");
  await page.getByLabel("После оплаты рекламного лота",{exact:true}).fill("Получено {amount} ₽. {url} Код: {code}");
  await page.getByLabel("После оплаты доплаты",{exact:true}).fill("Доплата {amount} ₽ получена");
  await page.getByRole("button",{name:"Сохранить настройки каталога",exact:true}).click();
  await expect.poll(()=>writes.filter(w=>w.path==="/settings").length).toBe(1);
  expect(writes.find(w=>w.path==="/settings")?.body).toEqual({catalog_public_url:"http://127.0.0.1:3180",catalog_promo_paid_template:"Получено {amount} ₽. {url} Код: {code}",catalog_topup_paid_template:"Доплата {amount} ₽ получена"});
});

test("catalog codes can be created, filtered, edited, logged and deleted",async({page})=>{
  await mock(page);
  let code:any=null;
  const events:any[]=[];
  await page.route("**/api/v1/catalog-codes**",async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname.endsWith("/events"))return route.fulfill({json:{items:events}});
    if(req.method()==="POST"){
      const data=req.postDataJSON();expect(data).toEqual({amount_rub:"1500",recipient:"buyer",lot_id:null});
      code={id:"test-code",code:"ABCD-EFGH",valid:true,state:"AWAITING_SELECTION",recipient:data.recipient,paid_rub:data.amount_rub,balance_rub:data.amount_rub,collection:"Liberty Figure",created_at:Date.now()/1000,locked:false,url:"http://127.0.0.1:3180/#code=ABCD-EFGH"};
      return route.fulfill({json:code});
    }
    if(req.method()==="PATCH"){
      const body=req.postDataJSON();expect(body).toEqual({balance_rub:"500",note:"Исправление"});
      events.push({id:"event",at:Date.now()/1000,action:"BALANCE_CHANGED",before_rub:"1500",after_rub:body.balance_rub,note:body.note});
      code={...code,balance_rub:body.balance_rub};return route.fulfill({json:code});
    }
    if(req.method()==="DELETE"){code={...code,valid:false,state:"DELETED"};return route.fulfill({json:code});}
    const status=url.searchParams.get("status");
    return route.fulfill({json:{items:code && (status==="all" || code.valid===(status==="valid"))?[code]:[],counts:{all:code?1:0,valid:code?.valid?1:0,invalid:code && !code.valid?1:0}}});
  });
  await page.goto("/catalog");
  const panel=page.locator("section.panel").filter({has:page.getByRole("heading",{name:"Коды каталога",exact:true})});
  await panel.getByLabel("Сумма кода, ₽").fill("1500");
  await panel.getByLabel("Telegram-получатель кода").fill("@buyer");
  await expect(panel.getByRole("button",{name:"Создать код",exact:true})).toBeEnabled();
  await panel.getByRole("button",{name:"Создать код",exact:true}).click();
  await expect(panel.getByText("Код создан",{exact:true})).toBeVisible();
  await panel.getByRole("button",{name:"Баланс",exact:true}).click();
  const edit=page.getByRole("dialog",{name:"Изменить баланс кода"});
  await edit.getByLabel("Новый доступный баланс, ₽").fill("500");
  await edit.getByLabel("Комментарий изменения").fill("Исправление");
  await edit.getByRole("button",{name:"Сохранить баланс"}).click();
  await expect(panel.getByText("Баланс: 500 ₽",{exact:true})).toBeVisible();
  await panel.getByRole("button",{name:"Логи",exact:true}).click();
  const logs=page.getByRole("dialog",{name:"Логи кода"});
  await expect(logs.getByText("Баланс изменён владельцем",{exact:true})).toBeVisible();
  await expect(logs.getByText("Исправление",{exact:true})).toBeVisible();
  await logs.getByRole("button",{name:"Закрыть",exact:true}).last().click();
  await panel.screenshot({path:"../data/qa/catalog-codes-desktop.png"});
  await panel.getByRole("button",{name:"Удалить",exact:true}).click();
  await page.getByRole("button",{name:"Удалить и заблокировать",exact:true}).click();
  await panel.getByLabel("Состояние кодов").selectOption("invalid");
  await expect(panel.getByText("Удалён",{exact:true})).toBeVisible();
  await expect(panel.getByRole("button",{name:"Баланс",exact:true})).toBeDisabled();
  await panel.getByLabel("Состояние кодов").selectOption("valid");
  await expect(panel.getByText("Кодов пока нет",{exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await panel.getByLabel("Состояние кодов").selectOption("all");
  await expect.poll(()=>page.locator(".sidebar").evaluate(el=>el.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
  await panel.screenshot({path:"../data/qa/catalog-codes-mobile.png",animations:"disabled"});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

for(const scheme of ["dark","light"] as const) test(`lot tabs and search match ${scheme} theme`, async ({ page }) => {
  await mock(page);
  await page.emulateMedia({colorScheme:scheme});
  await page.goto("/lots");
  const search=page.getByPlaceholder("Название, коллекция или модель",{exact:true});
  if(scheme==="dark"){
    await expect(page.locator(".tabs")).toHaveCSS("background-color","rgb(38, 41, 48)");
    await expect(page.getByRole("button",{name:"Активные",exact:true})).toHaveCSS("background-color","rgb(35, 71, 134)");
    await expect(page.locator(".search-box")).toHaveCSS("background-color","rgb(20, 21, 25)");
    await expect(search).toHaveCSS("background-color","rgba(0, 0, 0, 0)");
    await expect(page.getByRole("button",{name:`Рассчитать цену ${lot.title}`,exact:true})).toHaveCSS("background-color","rgb(38, 41, 48)");
  }else{
    await expect(page.locator(".search-box")).toHaveCSS("background-color","rgb(255, 255, 255)");
  }
  await search.focus();
  await page.screenshot({path:`../data/qa/lots-${scheme}-filters.png`,fullPage:true});
  await search.fill("nonexistent-gift");
  await expect(page.getByText("Объявления не найдены",{exact:true})).toBeVisible();
  await search.fill("");
  await expect(page.getByRole("row").filter({hasText:lot.title})).toBeVisible();
});

test("theme toggle persists across pages and styles forms and dialogs", async ({ page }) => {
  const writes=await mock(page);
  await page.emulateMedia({colorScheme:"light"});
  await page.goto("/");
  await expect(page.locator(".brand-logo img")).toHaveAttribute("src","/playerok-logo.png");
  await page.getByRole("button",{name:"Включить тёмную тему",exact:true}).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme","dark");
  expect(await page.evaluate(()=>localStorage.getItem("playerok.theme"))).toBe("dark");
  await page.getByRole("heading",{name:"Обзор",exact:true}).hover();
  await expect(page.locator(".theme-toggle")).toHaveCSS("background-color","rgb(28, 30, 35)");
  await page.screenshot({path:"../data/qa/dark-overview.png",fullPage:true});
  await page.goto("/gifts");
  await expect(page.getByRole("cell",{name:"Obsidian",exact:true})).toHaveCSS("color","rgb(238, 240, 245)");
  await page.screenshot({path:"../data/qa/dark-gifts.png",fullPage:true});
  await page.goto("/settings");
  await expect(page.getByRole("button",{name:"Включить светлую тему",exact:true})).toBeVisible();
  await expect(page.getByLabel("Стоимость одного Star, ₽",{exact:true})).toHaveCSS("background-color","rgb(20, 21, 25)");
  await page.getByRole("region",{name:"Режим работы",exact:true}).screenshot({path:"../data/qa/dark-work-mode.png"});
  await page.route(`**/api/v1/pricing/${lotId}/preview`,route=>route.fulfill({json:{
    id:"dark-price",action:"price",state:"PREVIEW",payload:{lot_id:lotId,price:550},
    expected:{purchase_rub:412,transfer_rub:31.5,profit_rub:21,promotion_rub:13,fees_rub:72,ton_rub:98}
  }}));
  await page.goto("/lots");
  await page.getByRole("button",{name:`Рассчитать цену ${lot.title}`,exact:true}).click();
  await expect(page.getByRole("dialog")).toHaveCSS("background-color","rgb(28, 30, 35)");
  await page.screenshot({path:"../data/qa/dark-calculation.png",fullPage:true});
  await page.goto("/settings");
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:"../data/qa/dark-mobile.png",fullPage:false});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByRole("button",{name:"Включить светлую тему",exact:true}).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme","light");
  expect(writes).toHaveLength(0);
});

test("theme follows system until an explicit theme is saved", async ({ page }) => {
  await mock(page);
  await page.emulateMedia({colorScheme:"dark"});
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme","dark");
  await page.emulateMedia({colorScheme:"light"});
  await expect(page.locator("html")).toHaveAttribute("data-theme","light");
  await page.getByRole("button",{name:"Включить тёмную тему",exact:true}).click();
  await page.emulateMedia({colorScheme:"dark"});
  await page.emulateMedia({colorScheme:"light"});
  await expect(page.locator("html")).toHaveAttribute("data-theme","dark");
});

test("work mode cards save permissions and adapt to mobile", async ({ page }) => {
  await mock(page);
  let current={...settings};
  const writes:any[]=[];
  await page.route("**/api/v1/settings",async route=>{
    if(route.request().method()==="PATCH"){
      const body=route.request().postDataJSON();writes.push(body);current={...current,...body};
    }
    return route.fulfill({json:current});
  });
  await page.goto("/settings");
  const mode=page.getByRole("region",{name:"Режим работы",exact:true});
  await expect(mode.getByRole("checkbox")).toHaveCount(7);
  await expect(mode.getByText("Подключения в порядке",{exact:true})).toBeVisible();
  const auto=mode.getByRole("checkbox",{name:"Автоматическое изменение цен",exact:true});
  await auto.focus();await page.keyboard.press("Space");
  await expect(auto).toBeChecked();
  await expect(mode.getByText("Есть изменения",{exact:true})).toBeVisible();
  expect(writes).toHaveLength(0);
  for(const name of ["Платные поднятия","Покупка NFT на MRKT","Передача NFT покупателям","Выдача отдельно протестирована мной"])
    await mode.getByRole("checkbox",{name,exact:true}).check();
  await page.getByRole("button",{name:"Сохранить настройки",exact:true}).click();
  await expect.poll(()=>writes.length).toBe(1);
  expect(writes[0]).toEqual({repricing_enabled:true,promotions_enabled:true,purchases_enabled:true,transfers_enabled:true,delivery_tested:true});
  await expect(mode.getByText("Подключения в порядке",{exact:true})).toBeVisible();
  await mode.screenshot({path:"../data/qa/work-mode-desktop.png"});
  await page.reload();
  await expect(auto).toBeChecked();
  await page.setViewportSize({width:390,height:844});
  await mode.screenshot({path:"../data/qa/work-mode-mobile.png"});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await expect(mode.getByRole("checkbox",{name:"Аварийная остановка",exact:true})).not.toBeChecked();
});

test("work mode never reports ready when worker heartbeat is stale", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/connections",route=>route.fulfill({json:{...connections,worker:{status:"IDLE",heartbeat:1}}}));
  await page.goto("/settings");
  const mode=page.getByRole("region",{name:"Режим работы",exact:true});
  await expect(mode.getByText("Требуется проверка",{exact:true})).toBeVisible();
});

test("completed order card displays actual profit", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/orders",route=>route.fulfill({json:{items:[{
    id:"completed",title:"Gift",stage:"COMPLETE",sale_rub:"200",profit_rub:"12",
    profit_actual:true,retry_allowed:false
  }],operations:[]}}));
  await page.goto("/orders");
  await page.getByRole("button",{name:"Подробнее",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Карточка заказа"});
  await expect(dialog.getByText("Фактическая прибыль",{exact:true})).toBeVisible();
  await expect(dialog.getByText("12 ₽",{exact:true})).toBeVisible();
  await expect(dialog.getByText("По учтённым расходам заказа",{exact:true})).toBeVisible();
});

test("stale TON rate preview shows profit but cannot apply the price", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/settings",route=>route.fulfill({json:{...settings,repricing_enabled:true}}));
  await page.route(`**/api/v1/pricing/${lotId}/preview`,route=>route.fulfill({json:{
    id:"stale-fx-preview",action:"price",state:"PREVIEW",payload:{lot_id:lotId,price:1500},
    expected:{profit_rub:100,purchase_rub:1100,ton_rub:100,fx_stale:true,
      fx_warning:"Не удалось обновить курс TON. Расчёт выполнен по последнему сохранённому курсу; он устарел."}
  }}));
  await page.goto("/lots");
  await page.getByRole("button",{name:`Рассчитать цену ${lot.title}`,exact:true}).click();
  const dialog=page.getByRole("dialog");
  await expect(dialog.getByText("100 ₽",{exact:true})).toHaveCount(2);
  await expect(dialog.getByText("Курс TON · устарел",{exact:true})).toBeVisible();
  await expect(dialog.getByText(/Не удалось обновить курс TON/)).toBeVisible();
  await expect(dialog.getByRole("button",{name:"Подтвердить",exact:true})).toBeDisabled();
  await page.screenshot({path:"../data/qa/stale-ton-preview.png",fullPage:true});
});

test("calculation dialog saves premium and refreshes profit immediately", async ({ page }) => {
  const writes=await mock(page);
  let planned=false;
  await page.route(`**/api/v1/pricing/${lotId}/premium`,async route=>{
    const body=route.request().postDataJSON();planned=body.premium_planned;
    writes.push({path:`/pricing/${lotId}/premium`,body});
    return route.fulfill({json:{...lot,premium_planned:planned}});
  });
  await page.route(`**/api/v1/pricing/${lotId}/preview`,route=>route.fulfill({json:{
    id:planned?"with-premium":"without-premium",action:"price",state:"PREVIEW",
    payload:{lot_id:lotId,price:planned?1513:1500},
    expected:{premium_planned:planned,premium_included:planned,premium_count:planned?1:0,
      premium_unit_rub:13,promotion_rub:planned?13:0,profit_rub:planned?87:100,purchase_rub:1100}
  }}));
  await page.goto("/lots");
  await page.locator(".binding-button").click();
  await expect(page.getByRole("checkbox",{name:/Считать как премиум/})).toHaveCount(0);
  await page.getByRole("dialog").locator("header").getByRole("button",{name:"Закрыть",exact:true}).click();
  await page.getByRole("button",{name:`Рассчитать цену ${lot.title}`,exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Применить рассчитанную цену"});
  await dialog.getByRole("checkbox",{name:/Считать как премиум/}).check();
  await expect(dialog.getByText("87 ₽",{exact:true})).toBeVisible();
  await expect(dialog.getByText("1 поднят. × 13 ₽",{exact:true})).toBeVisible();
  await page.screenshot({path:"../data/qa/planned-premium.png",fullPage:true});
  await dialog.locator("footer").getByRole("button",{name:"Закрыть",exact:true}).click();
  await page.getByRole("button",{name:`Рассчитать цену ${lot.title}`,exact:true}).click();
  await expect(dialog.getByRole("checkbox",{name:/Считать как премиум/})).toBeChecked();
  await dialog.getByRole("checkbox",{name:/Считать как премиум/}).uncheck();
  await expect(dialog.getByText("100 ₽",{exact:true})).toBeVisible();
  expect(writes.some(r=>r.path==="/operations/execute")).toBe(false);
});

test("manual TON exchange rate saves and persists with Stars cost", async ({ page }) => {
  await mock(page);
  let current:any={...settings,ton_rate_mode:"auto",manual_ton_rub:null};
  const writes:any[]=[];
  await page.route("**/api/v1/settings",async route=>{
    if(route.request().method()==="PATCH"){
      const body=route.request().postDataJSON();writes.push(body);current={...current,...body};
    }
    return route.fulfill({json:current});
  });
  await page.goto("/settings");
  await expect(page.getByLabel("Свой курс TON, ₽",{exact:true})).toHaveCount(0);
  await page.getByLabel("Источник курса TON",{exact:true}).selectOption("manual");
  await page.getByLabel("Свой курс TON, ₽",{exact:true}).fill("85.25");
  await page.getByLabel("Стоимость одного Star, ₽",{exact:true}).fill("1.26");
  await page.getByRole("button",{name:"Сохранить настройки",exact:true}).click();
  await expect.poll(()=>writes).toEqual([{ton_rate_mode:"manual",manual_ton_rub:"85.25",stars_rub:"1.26"}]);
  await page.reload();
  await expect(page.getByLabel("Свой курс TON, ₽",{exact:true})).toHaveValue("85.25");
  await page.screenshot({path:"../data/qa/manual-ton-rate.png",fullPage:true});
  await page.getByLabel("Источник курса TON",{exact:true}).selectOption("auto");
  await page.getByRole("button",{name:"Сохранить настройки",exact:true}).click();
  await expect.poll(()=>writes.length).toBe(2);
  expect(writes[1]).toEqual({ton_rate_mode:"auto"});
});

test("commission editor saves fixed TON fee", async ({ page }) => {
  const writes=await mock(page);
  await page.goto("/fees");
  await page.getByRole("button",{name:"Комиссия",exact:true}).click();
  await page.getByLabel("Тип",{exact:true}).selectOption("ton");
  await page.getByLabel("Сумма, TON",{exact:true}).fill("0.25");
  await page.getByRole("button",{name:"Сохранить профиль",exact:true}).click();
  await expect.poll(()=>writes.filter(r=>r.path==="/fee-profiles/default").length).toBe(1);
  expect(writes.find(r=>r.path==="/fee-profiles/default")?.body.fees[0]).toMatchObject({type:"ton",value:"0.25"});
  await page.screenshot({path:"../data/qa/ton-fee.png",fullPage:true});
});

test("zero expense limits save and are described as unlimited", async ({ page }) => {
  await mock(page);
  let current={...settings,purchase_limit_rub:"10000",daily_purchase_limit_rub:"10000",daily_promotion_limit_rub:"10000"};
  const writes:any[]=[];
  await page.route("**/api/v1/settings",async route=>{
    if(route.request().method()==="PATCH"){
      const body=route.request().postDataJSON();writes.push(body);current={...current,...body};
    }
    return route.fulfill({json:current});
  });
  await page.goto("/settings");
  await expect(page.getByText("0 — без ограничений. Разрешения на расходы включаются отдельно.",{exact:true})).toBeVisible();
  for(const label of ["Закупка на заказ, ₽","Закупки за сутки, ₽","Премиум за сутки, ₽"])
    await page.getByLabel(label,{exact:true}).fill("0");
  await page.getByRole("button",{name:"Сохранить настройки",exact:true}).click();
  await expect.poll(()=>writes).toEqual([{purchase_limit_rub:"0",daily_purchase_limit_rub:"0",daily_promotion_limit_rub:"0"}]);
  await page.reload();
  await expect(page.getByLabel("Закупка на заказ, ₽",{exact:true})).toHaveValue("0");
});

test("buyer reply templates save independently and persist after reload", async ({ page }) => {
  await mock(page);
  let current = { ...settings, buyer_paid_enabled:false,buyer_delivered_enabled:false,
    buyer_paid_template:"",buyer_delivered_template:"" };
  const writes:any[]=[];
  await page.route("**/api/v1/settings", async route => {
    if (route.request().method()==="PATCH") {
      const body=route.request().postDataJSON();writes.push(body);current={...current,...body};
    }
    return route.fulfill({json:current});
  });
  await page.goto("/settings");
  await page.getByLabel("Шаблон после оплаты",{exact:true}).fill("Спасибо за оплату заказа {order_id}!");
  await page.getByLabel("Шаблон после передачи NFT",{exact:true}).fill("Передал {gift}: {gift_link}");
  await page.getByText("Ответ после оплаты",{exact:true}).click();
  await page.getByText("Ответ после передачи NFT",{exact:true}).click();
  await page.getByRole("button",{name:"Сохранить настройки",exact:true}).click();
  await expect.poll(()=>writes.length).toBe(1);
  expect(writes[0]).toEqual({buyer_paid_template:"Спасибо за оплату заказа {order_id}!",
    buyer_delivered_template:"Передал {gift}: {gift_link}",buyer_paid_enabled:true,buyer_delivered_enabled:true});
  await page.reload();
  await expect(page.getByLabel("Шаблон после оплаты",{exact:true})).toHaveValue("Спасибо за оплату заказа {order_id}!");
  await expect(page.getByRole("checkbox",{name:"Ответ после передачи NFT",exact:true})).toBeChecked();
});

test("order card retries a paused order through the process task", async ({ page }) => {
  await mock(page);
  const writes: string[] = [];
  const order = { id:"retry-order", title:"Chill Flame", stage:"AWAITING_ENABLE", recipient:"buyer",sale_rub:"565",reason:"Реальная закупка и выдача выключены" };
  await page.route("**/api/v1/orders", route => route.fulfill({ json:{ items:[order],operations:[] } }));
  await page.route("**/api/v1/orders/retry-order/process", route => {
    writes.push(route.request().method());
    return route.fulfill({ json:{ task_id:"retry-task" } });
  });
  await page.route("**/api/v1/tasks/retry-task", route => route.fulfill({ json:{ state:"COMPLETE",result:{} } }));
  await page.goto("/orders");
  await page.getByRole("button", { name:"Подробнее",exact:true }).click();
  await page.getByRole("button", { name:"Повторить",exact:true }).click();
  await expect.poll(() => writes).toEqual(["POST"]);
  await expect(page.getByRole("dialog",{name:"Карточка заказа"})).toBeVisible();
  await expect(page.getByText("Заказ отправлен на проверку; убыточная закупка запрещена",{exact:true})).toBeVisible();
});

test("order retry is disabled when an earlier transfer needs reconciliation", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/orders", route => route.fulfill({ json:{
    items:[{id:"retry-order", title:"Chill Flame",stage:"WITHDRAWN",recipient:"buyer"}],
    operations:[{id:"retry-order:transfer",order_id:"retry-order",action:"order.transfer",state:"UNKNOWN"}]
  } }));
  await page.goto("/orders");
  await page.getByRole("button",{name:"Подробнее",exact:true}).click();
  await expect(page.getByRole("button",{name:"Повторить",exact:true})).toBeDisabled();
});

test("manual order before purchase has an enabled retry despite a manual hold", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/orders", route => route.fulfill({ json:{
    items:[{id:"retry-order",title:"Chill Flame",stage:"MANUAL",manual_hold:true,retry_allowed:true,
      reason:"Закупка не обеспечивает заданную прибыль"}],
    operations:[{id:"retry-order:buy",order_id:"retry-order",action:"order.buy",state:"FAILED"}]
  } }));
  await page.goto("/orders");
  await page.getByRole("button",{name:"Подробнее",exact:true}).click();
  await expect(page.getByRole("button",{name:"Повторить",exact:true})).toBeEnabled();
});

test("dashboard connection setup completes Telegram code and 2FA steps", async ({ page }) => {
  await mock(page);
  const writes: { path: string; body: any }[] = [];
  let login: any = { state: "IDLE" };
  let config: any = { api_id: "12345", api_hash_configured: true, user_session_configured: false, playerok_user_agent: "Mozilla/5.0 Test", playerok_request_timeout: 20 };
  await page.route("**/api/v1/connections/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() !== "GET") writes.push({ path, body: request.postDataJSON() });
    if (path.endsWith("/config")) return route.fulfill({ json: config });
    if (path.endsWith("/start")) login = { state: "CODE_REQUIRED", flow_id: "example-flow" };
    if (path.endsWith("/code")) login = { state: "PASSWORD_REQUIRED", flow_id: "example-flow" };
    if (path.endsWith("/password")) {
      login = { state: "AUTHORIZED", flow_id: "example-flow" };
      config = { ...config, user_session_configured: true, telegram_account: { username: "example_account" } };
    }
    return route.fulfill({ json: login });
  });
  await page.goto("/settings");
  await page.getByLabel("Номер Telegram", { exact: true }).fill("+79990000001");
  await page.getByRole("button", { name: "Получить код", exact: true }).click();
  await page.getByLabel("Код из Telegram", { exact: true }).fill("12345");
  await page.getByRole("button", { name: "Подтвердить код", exact: true }).click();
  await page.getByLabel("Пароль двухэтапной проверки Telegram", { exact: true }).fill("example-password");
  await page.getByRole("button", { name: "Подтвердить пароль", exact: true }).click();
  await expect(page.getByText("@example_account", { exact: true })).toBeVisible();
  expect(writes.find(w => w.path.endsWith("/code"))?.body).toEqual({ flow_id: "example-flow", value: "12345" });
  await expect(page.getByLabel("Telegram API Hash", { exact: true })).toHaveValue("");
  await expect(page.getByRole("link", { name: "Пошаговая инструкция" })).toHaveAttribute("href", "/guide.html");
  await page.screenshot({ path: "../data/qa/connection-settings.png", fullPage: true });
});

test("secret fields stay empty and account changes require pausing active trading", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/settings", route => route.fulfill({ json: { ...settings, purchases_enabled: true } }));
  await page.route("**/api/v1/connections/config", route => route.fulfill({ json: { bot_configured: true, api_hash_configured: true, api_id: "12345", owner_username: "example_owner" } }));
  await page.goto("/settings");
  await expect(page.getByLabel("Токен Telegram-бота", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Telegram API Hash", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "Сохранить бота", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Сохранить ключи", exact: true })).toBeDisabled();
});

test("Telegram drafts can be entered during trading and saved only after explicit pause",async({page})=>{
  await mock(page);
  let current={...settings,purchases_enabled:true};const writes:any[]=[];
  await page.route("**/api/v1/settings",route=>route.fulfill({json:current}));
  await page.route("**/api/v1/automation/stop",route=>{
    writes.push({path:"stop"});current={...current,emergency_stop:true};return route.fulfill({json:current});
  });
  await page.route("**/api/v1/connections/config",route=>{
    if(route.request().method()==="PATCH")writes.push({path:"keys",body:route.request().postDataJSON()});
    return route.fulfill({json:{api_id:"12345",api_hash_configured:true,user_session_configured:true}});
  });
  await page.goto("/settings");
  await page.getByLabel("Telegram API ID",{exact:true}).fill("54321");
  await page.getByLabel("Telegram API Hash",{exact:true}).fill("0123456789abcdef0123456789abcdef");
  await page.getByLabel("Номер Telegram",{exact:true}).fill("+79990000001");
  await expect(page.getByRole("button",{name:"Сохранить ключи",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Получить код",exact:true})).toBeDisabled();
  expect(writes).toEqual([]);
  await page.getByRole("button",{name:"Приостановить автоматизацию",exact:true}).click();
  await expect(page.getByRole("button",{name:"Сохранить ключи",exact:true})).toBeEnabled();
  await expect(page.getByLabel("Номер Telegram",{exact:true})).toHaveValue("+79990000001");
  await page.getByRole("button",{name:"Сохранить ключи",exact:true}).click();
  await expect.poll(()=>writes).toEqual([{path:"stop"},{path:"keys",body:{api_id:54321,api_hash:"0123456789abcdef0123456789abcdef"}}]);
});

test("Chrome delivery setup saves the transport and shows a one-time pairing code", async ({ page }) => {
  await mock(page);
  let current = { ...settings, delivery_transport: "mtproto", delivery_tested: true };
  const changes: any[] = [];
  await page.route("**/api/v1/settings", async route => {
    if (route.request().method() === "PATCH") { const body = route.request().postDataJSON(); changes.push(body); current = { ...current, ...body, delivery_tested: false }; }
    return route.fulfill({ json: current });
  });
  await page.route("**/api/v1/browser-delivery", route => route.fulfill({ json: { paired: false, ready: false, online: false, jobs: [] } }));
  await page.route("**/api/v1/browser-delivery/pairing", route => route.fulfill({ json: { code: "example-one-time-pairing-code", expires_in: 300 } }));
  await page.goto("/settings");
  await page.getByLabel("Способ передачи NFT", { exact: true }).selectOption("chrome_extension");
  await expect.poll(() => changes).toEqual([{ delivery_transport: "chrome_extension" }]);
  await expect(page.getByLabel("Способ передачи NFT", { exact: true })).toHaveValue("chrome_extension");
  await page.getByRole("button", { name: "Получить код подключения", exact: true }).click();
  await expect(page.getByLabel("Одноразовый код для расширения", { exact: true })).toHaveValue("example-one-time-pairing-code");
  await expect(page.getByRole("link", { name: "Скачайте расширение", exact: true })).toHaveAttribute("href", "/api/v1/browser-delivery/extension.zip");
});

test("Kurigram delivery saves the transport and explains reuse of the Telegram account", async ({ page }) => {
  await mock(page);
  let current = { ...settings, delivery_transport: "mtproto", delivery_tested: true };
  const changes: any[] = [];
  await page.route("**/api/v1/settings", async route => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON();
      changes.push(body);
      current = { ...current, ...body, delivery_tested: false };
    }
    return route.fulfill({ json: current });
  });
  await page.goto("/settings");
  await page.getByLabel("Способ передачи NFT", { exact: true }).selectOption("kurigram");
  await expect.poll(() => changes).toEqual([{ delivery_transport: "kurigram" }]);
  await expect(page.getByLabel("Способ передачи NFT", { exact: true })).toHaveValue("kurigram");
  await expect(page.getByText(/Kurigram использует аккаунт из настроек/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Получить код подключения", exact: true })).toBeHidden();
});

test("saving trading settings does not revert the separately saved transport or test attestation", async ({ page }) => {
  await mock(page);
  let current = { ...settings, delivery_transport: "chrome_extension", delivery_tested: true };
  const changes: any[] = [];
  await page.route("**/api/v1/settings", async route => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON();
      changes.push(body);
      const switched = body.delivery_transport && body.delivery_transport !== current.delivery_transport;
      current = { ...current, ...body, ...(switched ? { delivery_tested: false } : {}) };
    }
    return route.fulfill({ json: current });
  });
  await page.goto("/settings");
  await page.getByLabel("Закупка на заказ, ₽", { exact: true }).fill("1234");
  await page.getByLabel("Способ передачи NFT", { exact: true }).selectOption("kurigram");
  await expect(page.getByLabel("Способ передачи NFT", { exact: true })).toHaveValue("kurigram");
  await expect(page.getByRole("checkbox", { name: /^Выдача отдельно протестирована мной/ })).not.toBeChecked();
  await expect(page.getByLabel("Закупка на заказ, ₽", { exact: true })).toHaveValue("1234");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect.poll(() => changes).toEqual([{ delivery_transport: "kurigram" }, { purchase_limit_rub: "1234" }]);
  await page.reload();
  await expect(page.getByLabel("Способ передачи NFT", { exact: true })).toHaveValue("kurigram");
  await expect(page.getByRole("checkbox", { name: /^Выдача отдельно протестирована мной/ })).not.toBeChecked();
});

test("update confirmation refreshes a cached version before showing the target", async ({ page }) => {
  await mock(page);
  let latest="b".repeat(40),checks=0,installs=0;
  await page.route("**/api/v1/updates**",async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith("/check")){
      checks++;latest="c".repeat(40);
      return route.fulfill({json:{task_id:"task"}});
    }
    if(path.endsWith("/install"))installs++;
    return route.fulfill({json:{available:true,dismissed:false,current_commit:"a".repeat(40),latest_commit:latest,
      message:latest==="b".repeat(40)?"Old cached update":"Newest update",install_supported:true,progress:{state:"IDLE"}}});
  });
  await page.goto("/");
  await page.getByRole("button",{name:"Посмотреть обновление",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Установить обновление?"});
  await expect(dialog.getByText("Newest update",{exact:true})).toBeVisible();
  await expect(dialog.getByText("ccccccc",{exact:true})).toBeVisible();
  expect(checks).toBe(1);expect(installs).toBe(0);
});

test("update banner can be postponed and install requires a separate confirmation", async ({ page }) => {
  await mock(page);
  const writes: string[] = [];
  let dismissed = false;
  await page.route("**/api/v1/updates**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes.push(path);
    if (path.endsWith("/check")) return route.fulfill({json:{task_id:"task"}});
    if (path.endsWith("/dismiss")) dismissed = true;
    await route.fulfill({ json: { available: true, dismissed, current_commit: "a".repeat(40), latest_commit: "b".repeat(40), message: "Удобные обновления", commit_url: "https://github.com/DummyDa/playerok-manager/commit/" + "b".repeat(40), install_supported: true, progress: { state: "IDLE" } } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Посмотреть обновление", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Установить обновление?" })).toBeVisible();
  expect(writes.some(path => path.endsWith("/install"))).toBe(false);
  await page.getByRole("button", { name: "Не сейчас", exact: true }).click();
  await page.getByRole("button", { name: "Позже", exact: true }).click();
  await expect(page.getByRole("region", { name: "Обновление приложения" })).toHaveCount(0);
  expect(writes.some(path => path.endsWith("/install"))).toBe(false);
});

test("removed preview mode has no switch and calculation remains a preview", async ({ page }) => {
  await mock(page);
  await page.goto("/settings");
  await expect(page.getByText("Dry Run", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Обновления приложения", exact: true })).toBeVisible();
  await expect(page.getByText("Автоматическое изменение цен", { exact: true })).toBeVisible();
});

test("opening the installed version reloads the page and dismisses only that completion notice", async ({ page }) => {
  await mock(page);
  let completed = Date.now() / 1000;
  await page.route("**/api/v1/updates", route => route.fulfill({ json: { available: false, progress: { state: "COMPLETE", installed_commit: "b".repeat(40), updated_at: completed, message: "Обновление установлено" } } }));
  await page.goto("/settings");
  const button = page.getByRole("button", { name: "Открыть новую версию", exact: true });
  await expect(button).toBeVisible();
  await Promise.all([page.waitForEvent("load"), button.click()]);
  await expect(button).toHaveCount(0);
  await page.reload();
  await expect(button).toHaveCount(0);
  completed += 1;
  await page.reload();
  await expect(button).toBeVisible();
});

test("installation reconnects after repeated server outages and opens the ready version automatically", async ({ page }) => {
  await page.clock.install();
  await mock(page);
  const target = "b".repeat(40);
  let stage = "IDLE", offline = false, failures = 0, loads = 0, installs = 0;
  page.on("load", () => loads++);
  await page.route("**/api/v1/updates**", async route => {
    if (new URL(route.request().url()).pathname.endsWith("/check")) return route.fulfill({json:{task_id:"task"}});
    if (route.request().method() === "POST") { installs++; stage = "STOPPING"; return route.fulfill({ json: { started: true } }); }
    if (offline) { failures++; return route.fulfill({ status: 503, contentType: "text/plain", body: "Services restarting" }); }
    return route.fulfill({ json: { available: stage !== "COMPLETE", install_supported: true, current_commit: stage === "COMPLETE" ? target : "a".repeat(40), latest_commit: target, progress: { state: stage, target, installed_commit: stage === "COMPLETE" ? target : undefined, updated_at: Date.now() / 1000, message: "Завершаем работу служб" } } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Посмотреть обновление", exact: true }).click();
  await page.getByRole("button", { name: "Установить обновление", exact: true }).click();
  await expect(page.getByText("Новая версия откроется автоматически. Перезапускать приложение вручную не нужно.")).toBeVisible();
  offline = true;
  for (let i = 0; i < 4; i++) { await page.clock.fastForward(6000); await page.clock.runFor(100); }
  await expect.poll(() => failures).toBeGreaterThanOrEqual(3);
  await expect(page.getByText("Приложение временно недоступно: ожидаем завершения установки и запуска служб.")).toBeVisible();
  expect(loads).toBe(1);
  stage = "COMPLETE"; offline = false;
  const loaded = page.waitForEvent("load");
  await page.clock.fastForward(6000);
  await loaded;
  await expect(page.getByRole("button", { name: "Открыть новую версию", exact: true })).toHaveCount(0);
  await expect.poll(() => loads).toBe(2);
  expect(installs).toBe(1);
  expect(await page.evaluate(() => sessionStorage.getItem("playerok.update-pending"))).toBeNull();
});

test("failed background polls keep cached content and show only a quiet status after a sustained failure", async ({ page }) => {
  await page.clock.install();
  await mock(page);
  let failing = false, failures = 0;
  await page.route("**/api/v1/settings", async route => {
    if (failing) { failures++; await route.fulfill({ status: 500, contentType: "text/plain", body: "Internal Server Error" }); }
    else await route.fulfill({ json: settings });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Стоп", exact: true })).toBeEnabled();
  failing = true;
  await page.clock.fastForward(6000);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.clock.runFor(100);
  await expect.poll(() => failures).toBeGreaterThan(0);
  await expect(page.getByText(/Сервер временно недоступен \(HTTP 500\)/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Баланс Playerok", exact: true })).toBeVisible();
  await page.clock.fastForward(11000);
  await expect(page.getByText("Восстанавливаем связь с сервером. Пока показаны последние полученные данные.")).toBeVisible();
  failing = false;
  await page.clock.fastForward(20000);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.clock.runFor(100);
  await expect(page.getByText("Восстанавливаем связь с сервером. Пока показаны последние полученные данные.")).toHaveCount(0);
});

test("header stop button resumes automation on the same page without changing feature permissions", async ({ page }) => {
  await mock(page);
  let stopped = false;
  const patches: any[] = [];
  await page.route("**/api/v1/settings", async route => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON(); patches.push(body); stopped = body.emergency_stop;
    }
    await route.fulfill({ json: { ...settings, emergency_stop: stopped } });
  });
  await page.route("**/api/v1/automation/stop", async route => {
    stopped = true; await route.fulfill({ json: { emergency_stop: true } });
  });
  await page.goto("/lots");
  await page.getByRole("button", { name: "Стоп", exact: true }).click();
  const resume = page.getByRole("button", { name: "Возобновить", exact: true });
  await expect(resume).toBeEnabled();
  await resume.click();
  await expect(page.getByRole("button", { name: "Стоп", exact: true })).toBeEnabled();
  await expect(page).toHaveURL(/\/lots$/);
  expect(patches).toEqual([{ emergency_stop: false }]);
});

async function mock(page: Page) {
  const writes: { path: string; body: any }[] = [];
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname.replace("/api/v1", "");
    if (req.method() !== "GET") writes.push({ path: p, body: req.postDataJSON() });
    let result: any = {};
    if (p === "/auth/status") result = { initialized: true, authenticated: true, csrf: "test-csrf" };
    else if (p === "/settings") result = settings;
    else if (p === "/connections") result = connections;
    else if (p === "/connections/config") result = { bot_configured: false, api_hash_configured: false, playerok_user_agent: "Mozilla/5.0 ExampleBrowser", playerok_request_timeout: 20 };
    else if (p === "/connections/telegram-user/login") result = { state: "IDLE" };
    else if (p === "/dashboard") result = { active_lots: 46, inactive_lots: 3, orders: 11, problem_orders: 11, completed_orders: 0, revenue_rub: "0", profit_rub: "0", expenses_rub: "0", connections, events, quote_count: 1, fx: { rate: "138.4" } };
    else if (p === "/lots") result = { items: [lot] };
    else if (p === "/lots/capabilities") result = { manual_reason: "Непроверенные операции выполняются на Playerok." };
    else if (p === "/gift-pairs") result = { items: [pair], catalog: [{ id: "c", name: "Liberty Figure" }] };
    else if (p === "/gift-pairs/models") result = { items: [{ id: "m", name: "Obsidian" }] };
    else if (p === "/quotes") result = { items: [{ id: "pair", available: true, ton: "8.568", fetched_at: Date.now() / 1000, offers_in_page: 20 }], history: [8.8, 8.7, 8.9, 8.6, 8.568].map((ton, i) => ({ id: String(i), pair_id: "pair", available: true, ton: String(ton) })) };
    else if (p === "/fee-profiles") result = { items: [{ id: "default", name: "Основные комиссии", configured: false, fees: [] }] };
    else if (p === "/pricing") result = { strategy: { enabled: false, profit_type: "percent", profit_value: "5", profit_basis: "purchase", min_price: 0, max_price: null, round_step: 1, change_threshold: 1, use_competitor: false, competitor_mode: "cheaper", competitor_offset: "1", competitor_offset_type: "rub", competitor_id: null }, items: [] };
    else if (p === "/competitors") result = { items: [{ id: "rival", lot_id: lotId, pair_id: "pair", title: "Liberty Figure — Obsidian", price: 1459, enabled: true, confirmed: true, note: "Выбор фона", url: "https://playerok.com/products/rival" }] };
    else if (p === "/promotions") result = { items: [], operations: [] };
    else if (p === "/orders") result = { items: [{ id: "order", title: "Liberty Figure — Obsidian", stage: "MANUAL", recipient: "buyer", reason: "Импортировано из прототипа" }], operations: [] };
    else if (p === "/finance") result = { totals: { revenue: "0", purchase: "0", transfer: "0", fees: "0", promotion: "0" }, profit_rub: "0", items: [], by_lot: [], by_pair: [] };
    else if (p === "/events") result = { items: events };
    else if (p === "/automation") result = { worker: connections.worker, tasks: [] };
    else if (p === `/promotions/${lotId}/preview`) result = { task_id: "task" };
    else if (p === "/tasks/task") result = { state: "COMPLETE", result: preview };
    else if (p.startsWith("/fee-profiles/")) result = req.postDataJSON();
    await route.fulfill({ json: result });
  });
  return writes;
}

test("first setup screen does not expose imported secrets", async ({ page }) => {
  await page.route("**/api/v1/auth/status", route => route.fulfill({ json: { initialized: false, authenticated: false, csrf: null } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Ваше новое рабочее место" })).toBeVisible();
  await expect(page.getByLabel("Пароль", { exact: true })).toBeVisible();
  await page.screenshot({ path: "../data/qa/login-desktop.png", fullPage: true });
});

test("all sections render without browser errors", async ({ page }) => {
  await mock(page); const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const pages: Record<string, string> = { "": "Обзор", lots: "Лоты", orders: "Заказы", gifts: "NFT Gifts", catalog: "Каталог", pricing: "Автоцены", fees: "Комиссии", competitors: "Конкуренты", promotions: "Поднятия", finance: "Финансы", history: "История", settings: "Настройки" };
  for (const [path, title] of Object.entries(pages)) {
    await page.goto("/" + path, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: title, level: 1, exact: true })).toBeVisible();
  }
  expect(errors).toEqual([]);
  await page.goto("/");
  await expect(page.getByText("ExampleStore")).toBeVisible();
  await page.screenshot({ path: "../data/qa/dashboard-desktop.png", fullPage: true });
});

test("Playerok available and frozen balances are shown on overview and finance", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/connections", route => route.fulfill({ json: { ...connections, items: connections.items.map(c => c.id === "playerok" ? { ...c, balance: { available: 4.6, pendingIncome: 508.3, value: 512.9 } } : c) } }));
  for (const section of ["/", "/finance"]) {
    await page.goto(section);
    const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "Баланс Playerok", exact: true }) });
    await expect(panel.getByText("4,6 ₽", { exact: true })).toBeVisible();
    await expect(panel.getByText("508,3 ₽", { exact: true })).toBeVisible();
    await expect(panel.getByText("512,9 ₽", { exact: true })).toBeVisible();
    await expect(panel.getByText("В заморозке", { exact: true })).toBeVisible();
  }
});

test("missing Playerok balance is not presented as zero", async ({ page }) => {
  await mock(page);
  await page.goto("/finance");
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "Баланс Playerok", exact: true }) });
  await expect(panel.locator(".playerok-balance-grid strong")).toHaveText(["—", "—", "—"]);
});

test("premium preview cannot purchase when promotions are disabled", async ({ page }) => {
  const writes = await mock(page);
  await page.goto("/lots");
  await page.getByRole("button", { name: "Поднять Liberty Figure — Obsidian", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("25 ₽", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Подтвердить", exact: true })).toBeDisabled();
  expect(writes.some(r => r.path === "/operations/execute")).toBe(false);
  await page.screenshot({ path: "../data/qa/premium-preview.png", fullPage: true });
});

test("commission editor saves a structured fee profile", async ({ page }) => {
  const writes = await mock(page);
  await page.goto("/fees");
  await page.getByRole("button", { name: "Комиссия", exact: true }).click();
  await page.getByLabel("Название", { exact: true }).fill("Playerok");
  await page.getByLabel("Процент, %", { exact: true }).fill("10");
  await page.getByRole("button", { name: "Сохранить профиль" }).click();
  await expect.poll(() => writes.filter(r => r.path === "/fee-profiles/default").length).toBe(1);
  expect(writes.find(r => r.path === "/fee-profiles/default")?.body.fees[0].value).toBe("10");
});

test("plain text server failure shows a readable error without retrying a save", async ({ page }) => {
  await mock(page);
  let attempts = 0;
  await page.route("**/api/v1/fee-profiles/default", route => {
    attempts++;
    return route.fulfill({ status: 500, contentType: "text/plain", body: "Internal Server Error" });
  });
  await page.goto("/fees");
  await page.getByRole("button", { name: "Сохранить профиль" }).click();
  await expect(page.getByRole("status")).toContainText("Сервер временно недоступен (HTTP 500)");
  await expect(page.getByRole("status")).not.toContainText("Unexpected token");
  expect(attempts).toBe(1);
});

test("price calculation shows configuration errors beside the lots", async ({ page }) => {
  await mock(page);
  await page.route(`**/api/v1/pricing/${lotId}/preview`, route => route.fulfill({ status: 400, json: { detail: "Сначала настройте и подтвердите профиль комиссий" } }));
  await page.goto("/lots");
  await page.getByRole("button", { name: `Рассчитать цену ${lot.title}` }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Сначала настройте и подтвердите профиль комиссий" })).toContainText("Сначала настройте и подтвердите профиль комиссий");
  await expect(page.getByRole("alert").filter({ hasText: "Сначала настройте и подтвердите профиль комиссий" }).getByRole("link", { name: "Открыть комиссии" })).toHaveAttribute("href", "/fees");
  await expect(page.getByRole("button", { name: `Рассчитать цену ${lot.title}` })).toBeEnabled();
});

test("price calculation opens the preview without applying a price", async ({ page }) => {
  const writes = await mock(page);
  await page.route(`**/api/v1/pricing/${lotId}/preview`, route => route.fulfill({ json: { id: "price-op", action: "price", state: "PREVIEW", payload: { price: 1500 }, expected: { profit_rub: 100, purchase_rub: 1100, transfer_rub: 50, fees_rub: 250, ton_rub: 138 } } }));
  await page.goto("/lots");
  await page.getByRole("button", { name: `Рассчитать цену ${lot.title}` }).click();
  const dialog = page.getByRole("dialog", { name: "Применить рассчитанную цену" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Подтвердить", exact: true })).toBeDisabled();
  expect(writes.some(r => r.path === "/operations/execute")).toBe(false);
});

test("premium forecast settings save and the exact tariff appears in the calculation", async ({ page }) => {
  const writes = await mock(page);
  const estimate = { id: lotId, status: "READY", purchase_rub: "1000", transfer_rub: "50", promotion_rub: "120", premium_included: true, premium_count: 2, premium_unit_rub: "60", sale_price: 1400, profit_rub: "90", current_profit_rub: "-70", fees_rub: "140", ton_rub: "100" };
  await page.route("**/api/v1/pricing", route => route.fulfill({ json: { strategy: { profit_value: "5" }, items: [estimate] } }));
  await page.route(`**/api/v1/pricing/${lotId}/preview`, route => route.fulfill({ json: { id: "premium-op", action: "price", state: "PREVIEW", payload: { price: 1400 }, expected: estimate } }));
  await page.goto("/pricing");
  await expect(page.getByLabel("Премиум в себестоимости", { exact: true })).toHaveValue("auto");
  await page.getByLabel("Премиум в себестоимости", { exact: true }).selectOption("include");
  await page.getByLabel("Поднятий на одну продажу", { exact: true }).fill("2");
  await page.getByRole("button", { name: "Сохранить стратегию", exact: true }).click();
  await expect.poll(() => writes.find(w => w.path === "/pricing/strategy")?.body.premium_purchases_per_sale).toBe(2);
  expect(writes.find(w => w.path === "/pricing/strategy")?.body.premium_mode).toBe("include");
  const row = page.getByRole("row").filter({ hasText: lot.title });
  await expect(row.getByRole("cell", { name: "120 ₽", exact: true })).toBeVisible();
  await expect(row.getByRole("cell", { name: "-70 ₽", exact: true })).toHaveClass("red-text");
  await row.getByRole("button", { name: "Расчёт", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Применить рассчитанную цену" });
  await expect(dialog.getByText("Премиум в цене продажи", { exact: true })).toBeVisible();
  await expect(dialog.getByText("2 поднят. × 60 ₽", { exact: true })).toBeVisible();
  expect(writes.some(w => w.path === "/operations/execute")).toBe(false);
  await page.screenshot({ path: "../data/qa/premium-profit-preview.png", fullPage: true });
});

test("ordinary products keep management controls but have no NFT calculation", async ({ page }) => {
  const writes = await mock(page);
  const ordinary = { ...lot, id: "robux", title: "300 ROBUX", pair_id: null, pair: null, delivery_mode: "manual" };
  await page.route("**/api/v1/lots", route => route.fulfill({ json: { items: [ordinary, lot] } }));
  await page.goto("/lots");
  const row = page.getByRole("row").filter({ hasText: "300 ROBUX" });
  await expect(row.getByText("Не применяется", { exact: true })).toBeVisible();
  await expect(row.getByRole("button", { name: "Рассчитать цену 300 ROBUX" })).toHaveCount(0);
  await expect(row.getByRole("button", { name: "Редактировать 300 ROBUX" })).toBeVisible();
  await expect(row.getByRole("button", { name: "Поднять 300 ROBUX" })).toBeVisible();
  await row.getByRole("button", { name: /Обычный товар/ }).click();
  const dialog = page.getByRole("dialog", { name: "Тип товара и выдача" });
  await expect(dialog.getByLabel("Тип товара")).toHaveValue("manual");
  await expect(dialog.getByLabel("Коллекция — модель")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect.poll(() => writes.filter(r => r.path === "/lots/robux/binding").length).toBe(1);
  expect(writes.find(r => r.path === "/lots/robux/binding")?.body.delivery_mode).toBe("manual");
});

test("new lot can create and select a pair inline without losing its fields", async ({ page }) => {
  await mock(page);
  const added = { id: "new-inline-pair", collection: "Liberty Figure", model: "Fire Opal" };
  let created = false, posts = 0;
  await page.route("**/api/v1/gift-pairs", async route => {
    if (route.request().method() === "POST") {
      posts++; created = true;
      expect(route.request().postDataJSON()).toEqual({ collection: added.collection, model: added.model });
      return route.fulfill({ json: added });
    }
    return route.fulfill({ json: { items: created ? [pair, added] : [pair], catalog: [{ id: "c", name: "Liberty Figure" }] } });
  });
  await page.goto("/lots");
  await page.getByRole("button", { name: "Новый лот", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Новое объявление" });
  await dialog.getByLabel("Название", { exact: true }).fill("Мой новый лот");
  await dialog.getByLabel("Цена, ₽", { exact: true }).fill("1500");
  await dialog.getByRole("button", { name: "Создать новую пару" }).click();
  await dialog.getByLabel("Название коллекции", { exact: true }).fill(added.collection);
  await dialog.getByLabel("Модель", { exact: true }).fill(added.model);
  await dialog.getByRole("button", { name: "Создать и выбрать" }).click();
  await expect(dialog.getByLabel("Коллекция — модель")).toHaveValue(added.id);
  await expect(dialog.getByLabel("Название", { exact: true })).toHaveValue("Мой новый лот");
  await expect(dialog.getByLabel("Цена, ₽", { exact: true })).toHaveValue("1500");
    await dialog.getByRole("checkbox",{name:/Опубликовать с премиумом/}).click();
    await expect(dialog.getByLabel("Максимальная стоимость премиума, ₽")).toBeVisible();
    await dialog.getByLabel("Максимальная стоимость премиума, ₽").fill("50");
  await expect(page).toHaveURL(/\/lots$/);
  expect(posts).toBe(1);
});

test("pair can be created inline when binding an existing lot", async ({ page }) => {
  const writes = await mock(page);
  const added = { id: "inline-binding", collection: "Liberty Figure", model: "Gold" };
  await page.route("**/api/v1/gift-pairs", route => route.fulfill({ json: route.request().method() === "POST" ? added : { items: [pair], catalog: [] } }));
  await page.goto("/lots");
  await page.getByRole("row").filter({ hasText: lot.title }).locator(".binding-button").click();
  const dialog = page.getByRole("dialog", { name: "Тип товара и выдача" });
  await dialog.getByRole("button", { name: "Создать новую пару" }).click();
  await dialog.getByLabel("Название коллекции").fill(added.collection);
  await dialog.getByLabel("Модель", { exact: true }).fill(added.model);
  await dialog.getByRole("button", { name: "Создать и выбрать" }).click();
  await expect(dialog.getByLabel("Коллекция — модель")).toHaveValue(added.id);
  await dialog.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect.poll(() => writes.filter(r => r.path === `/lots/${lotId}/binding`).length).toBe(1);
  expect(writes.find(r => r.path === `/lots/${lotId}/binding`)?.body.pair_id).toBe(added.id);
});

test("promotion schedule applies common parameters to selected lots", async ({ page }) => {
  const writes = await mock(page);
  await page.route("**/api/v1/lots", route => route.fulfill({ json: { items: [lot, { ...lot, id: "second-lot", title: "Второй лот" }] } }));
  await page.goto("/promotions");
  await page.getByRole("button", { name: "Выбрать все", exact: true }).click();
  await page.getByLabel("Интервал, минут").fill("120");
  await page.getByLabel("Лимит одной покупки, ₽").fill("25");
  await expect(page.getByRole("button", { name: "Узнать стоимость", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Сохранить для выбранных · 2" }).click();
  await expect.poll(() => writes.filter(r => r.path === "/promotions/schedules/bulk").length).toBe(1);
  expect(writes.find(r => r.path === "/promotions/schedules/bulk")?.body).toEqual({ item_ids: [lotId, "second-lot"], interval: 7200, max_cost_rub: "25", enabled: false });
});

test("schedule deletion requires confirmation and removes only that schedule", async ({ page }) => {
  await mock(page);
  let exists = true, deletions = 0;
  await page.route("**/api/v1/promotions**", route => {
    if (route.request().method() === "DELETE") { exists = false; deletions++; return route.fulfill({ json: { deleted: true } }); }
    return route.fulfill({ json: { items: exists ? [{ id: lotId, enabled: false, interval: 3600, max_cost_rub: "25" }] : [], operations: [] } });
  });
  await page.goto("/promotions");
  await page.getByRole("button", { name: `Удалить расписание ${lot.title}`, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Удалить расписание?" });
  await expect(dialog).toBeVisible();
  expect(deletions).toBe(0);
  await dialog.getByRole("button", { name: "Удалить расписание", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Расписаний пока нет" })).toBeVisible();
  expect(deletions).toBe(1);
});

test("mobile layout fits the viewport and navigation works", async ({ page }) => {
  await mock(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Обзор", level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../data/qa/dashboard-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Меню", exact: true }).click();
  await page.getByRole("link", { name: "Лоты", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Лоты", level: 1 })).toBeVisible();
});

test("sidebar arrow reveals labels and remembers the expanded state", async ({ page }) => {
  await mock(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Все разделы" });
  const button = page.getByRole("button", { name: "Развернуть меню", exact: true });
  await expect(button).toHaveAttribute("aria-expanded", "false");
  const before = await navigation.boundingBox();
  await button.click();
  await expect(page.getByRole("button", { name: "Свернуть меню", exact: true })).toHaveAttribute("aria-expanded", "true");
  await expect.poll(async () => (await navigation.boundingBox())!.width).toBeGreaterThan(before!.width + 100);
  await expect(navigation.getByText("Настройки", { exact: true })).toHaveCSS("opacity", "1");
  await page.reload();
  await expect(page.getByRole("button", { name: "Свернуть меню", exact: true })).toHaveAttribute("aria-expanded", "true");
  await page.evaluate(() => {
    const node = document.querySelector(".sidebar")!;
    (window as any).sidebarBeforeNavigation = node;
    (window as any).sidebarTransitionCount = 0;
    node.addEventListener("transitionrun", event => { if (event.target === node && (event as TransitionEvent).propertyName === "width") (window as any).sidebarTransitionCount++; });
  });
  await navigation.getByRole("link", { name: "Лоты", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Лоты", exact: true, level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.querySelector(".sidebar") === (window as any).sidebarBeforeNavigation)).toBe(true);
  expect(await page.evaluate(() => (window as any).sidebarTransitionCount)).toBe(0);
  await expect(page.getByRole("button", { name: "Свернуть меню", exact: true })).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "Свернуть меню", exact: true }).click();
  await expect(page.getByRole("button", { name: "Развернуть меню", exact: true })).toHaveAttribute("aria-expanded", "false");
});

test("redesigned sections fit phone, tablet and desktop widths", async ({ page }) => {
  await mock(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const section of ["", "lots", "gifts", "fees", "settings"]) {
      await page.goto("/" + section);
      await expect(page.locator("h1")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${section || "overview"} at ${width}px`).toBe(true);
      if (section === "" || (width === 1440 && ["lots", "gifts", "settings"].includes(section))) {
        await page.screenshot({ path: `../data/qa/redesign-${section || "overview"}-${width}.png`, fullPage: true });
      }
    }
  }
});
