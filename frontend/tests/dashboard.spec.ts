import { test, expect, type Page } from "@playwright/test";
const lotId = "own-lot";
const settings = { dry_run: true, emergency_stop: false, repricing_enabled: false, purchases_enabled: false, transfers_enabled: false, promotions_enabled: false, notifications_enabled: false, delivery_tested: false,
  stars_rub: null, transfer_stars_reserve: 25, purchase_limit_rub: "0", daily_purchase_limit_rub: "0", daily_promotion_limit_rub: "0", quote_interval: 3600, competitor_interval: 900, quote_max_age: 7200,
  description_template: "Описание магазина", playerok_username: "ExampleStore", playerok_account_id: "seller", owner_chat_id: 1, orders_baselined: false };
const pair = { id: "pair", collection: "Liberty Figure", model: "Obsidian" };
const lot = { id: lotId, title: "Liberty Figure — Obsidian", description: "Пример объявления", pair_id: "pair", pair, status: "APPROVED", price: 800, raw_price: 1500, fee_profile_id: "default", url: "https://playerok.com/products/example", image: null };
const connections = { items: [{ id: "playerok", configured: true, status: "CONNECTED", last_success: Date.now() / 1000 }, { id: "mrkt", configured: true, status: "CONNECTED", mrkt_ton: "0", stars: 138 }, { id: "telegram", configured: true, status: "CONNECTED" }], worker: { status: "IDLE", heartbeat: Date.now() / 1000 } };
const preview = { id: "preview", state: "PREVIEW", action: "promotion", payload: { lot_id: lotId }, expected: { cost_rub: "25", balance: 100 } };
const events = [{ id: "one", title: "Лоты синхронизированы", detail: "46 объявлений обновлены", action: "sync", level: "INFO", updated_at: Date.now() / 1000 }];

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

test("secret fields stay empty and settings changes are blocked outside Dry Run", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/settings", route => route.fulfill({ json: { ...settings, dry_run: false } }));
  await page.route("**/api/v1/connections/config", route => route.fulfill({ json: { bot_configured: true, api_hash_configured: true, api_id: "12345", owner_username: "example_owner" } }));
  await page.goto("/settings");
  await expect(page.getByLabel("Токен Telegram-бота", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Telegram API Hash", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "Сохранить бота", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Сохранить ключи", exact: true })).toBeDisabled();
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
  const pages: Record<string, string> = { "": "Обзор", lots: "Лоты", orders: "Заказы", gifts: "NFT Gifts", pricing: "Автоцены", fees: "Комиссии", competitors: "Конкуренты", promotions: "Поднятия", finance: "Финансы", history: "История", settings: "Настройки" };
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

test("premium preview cannot purchase while Dry Run is active", async ({ page }) => {
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
