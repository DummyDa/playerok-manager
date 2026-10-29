/* WebA DOM adapter: every selection is exact, and uncertainty stops delivery. */
(() => {
  if (globalThis.__playerokDeliveryInstalled) return;
  globalThis.__playerokDeliveryInstalled = true;
  let active = false, cancelled = false, phase = "Подготовка", menuWasOpen = false;
  const visible = el => !!el && el.getBoundingClientRect().width > 0 && getComputedStyle(el).visibility !== "hidden" && getComputedStyle(el).display !== "none";
  const norm = text => text.toLowerCase().replace(/[^a-z0-9]/g, "");
  function plainText(el) {
    const copy = el.cloneNode(true);
    copy.querySelectorAll("svg, i, .icon, [aria-hidden=true], .ripple-container, canvas, img").forEach(node => node.remove());
    return (copy.textContent || "").replace(/[\uE000-\uF8FF\u200B]/g, "").replace(/\s+/g, " ").trim();
  }
  const label = el => el.getAttribute("aria-label")?.trim() || plainText(el);
  const profileItem = () => [...document.querySelectorAll(".MenuItem, .menu-item, [role=menuitem]")].find(el => visible(el) && (/^(my profile|мой профиль)$/i.test(label(el)) || /^(my profile|мой профиль)$/i.test(plainText(el))));
  function modal() {
    const dialogs = [...document.querySelectorAll(".Modal .modal-dialog, dialog.Modal[open] .modal-dialog, [role=dialog]")].filter(visible);
    return dialogs.at(-1);
  }
  async function wait(find, timeout = 25000) {
    const until = Date.now() + timeout;
    while (Date.now() < until) {
      if (cancelled) throw new Error("Выдача остановлена сервером");
      const result = find(); if (result) return result;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw new Error("Telegram Web: нужный элемент не найден. Интерфейс мог измениться");
  }
  function exactGift(dialog, slug) {
    if (!dialog) return false;
    if ([...dialog.querySelectorAll("a[href]")].some(a => a.href.split(/[?#]/)[0] === "https://t.me/nft/" + slug)) return true;
    const [name, number] = slug.split("-");
    return new RegExp(norm(name) + number + "(?![0-9])").test(norm(dialog.textContent));
  }
  function transferButton(dialog) {
    const buttons = [...(dialog?.querySelectorAll("button") || [])].filter(el => visible(el) && !el.disabled && /^(transfer|передать)(\s|$)/i.test(label(el)));
    return buttons.length === 1 ? buttons[0] : null;
  }
  function closeDialog(dialog) {
    const parent = dialog.closest(".Modal") || dialog;
    const buttons = [...parent.querySelectorAll("button")].filter(el => visible(el) && (/^(close|закрыть)$/i.test(el.getAttribute("aria-label") || "") || el.classList.contains("modal-close-button")));
    if (buttons.length !== 1) throw new Error("Не найдена однозначная кнопка закрытия карточки");
    buttons[0].click();
  }
  async function openOwnedGift(job) {
    const current = modal();
    if (current && exactGift(current, job.slug) && transferButton(current)) return current;
    if (current) {
      if (current.closest(".confirm")) throw new Error("В Telegram уже открыто другое подтверждение");
      closeDialog(current);
      await wait(() => !modal(), 5000);
    }
    phase = "Открытие меню Telegram";
    menuWasOpen = !!profileItem();
    if (!menuWasOpen) {
      const menu = await wait(() => [...document.querySelectorAll("#LeftMainHeader button, .main-menu button")].find(el => visible(el) && /^(open menu|открыть меню)$/i.test(el.getAttribute("aria-label") || "")));
      menu.click();
    }
    phase = "Открытие своего профиля";
    const profile = await wait(profileItem);
    profile.click();
    phase = "Открытие своих подарков";
    const cards = () => [...document.querySelectorAll("#RightColumn .interactive-gift.scroll-item, #RightColumn-wrapper .interactive-gift.scroll-item")].filter(visible);
    if (!cards().length) {
      const gifts = await wait(() => [...document.querySelectorAll("#RightColumn .Tab, #RightColumn [role=tab], #RightColumn button, #RightColumn-wrapper .Tab")].find(el => visible(el) && /^(gifts|подарки)(\s*\d+)?$/i.test(label(el))));
      gifts.click();
    }
    await wait(() => cards().length);
    phase = "Поиск конкретного NFT среди своих подарков";
    const seen = new WeakSet();
    for (let round = 0; round < 20; round++) {
      const fresh = cards().filter(el => !seen.has(el));
      if (!fresh.length) break;
      for (const card of fresh) {
        seen.add(card); card.click();
        const detail = await wait(() => modal(), 15000);
        if (exactGift(detail, job.slug) && transferButton(detail)) return detail;
        closeDialog(detail);
        await wait(() => !modal(), 5000);
      }
      const pane = cards().at(-1)?.closest(".custom-scroll");
      if (!pane || pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 2) break;
      pane.scrollBy({ top: Math.max(200, pane.clientHeight * 0.8), behavior: "instant" });
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error("Точный NFT не найден в собственных подарках; другой подарок не выбран");
  }
  function checkedConfirmation(job) {
    const dialog = modal(), button = transferButton(dialog);
    if (!button || !exactGift(dialog, job.slug)) return null;
    const peers = new Set([...dialog.querySelectorAll("[data-peer-id]")].map(el => el.getAttribute("data-peer-id")));
    if (peers.size !== 1 || !peers.has(String(job.recipient_id))) return null;
    const numbers = plainText(button).match(/\d[\d\s\u00a0\u202f]*/g) || [];
    const stars = numbers.length === 1 ? Number(numbers[0].replace(/\D/g, "")) : numbers.length === 0 ? 0 : NaN;
    if (stars !== job.stars) throw new Error("Стоимость передачи в Telegram не совпадает с расчётом");
    return { dialog, button, stars };
  }
  async function message(job, event, extra = {}) {
    const result = await chrome.runtime.sendMessage({ type: "event", job_id: job.id, lease: job.lease, event, ...extra });
    if (result?.error) throw new Error(result.error);
    return result;
  }
  async function prepare(job) {
    try {
      const card = await openOwnedGift(job);
      transferButton(card).click();
      phase = "Поиск получателя";
      const input = await wait(() => [...(modal()?.querySelectorAll("input") || [])].find(el => visible(el) && ["text", "search"].includes(el.type)));
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(input, job.recipient); input.dispatchEvent(new Event("input", { bubbles: true }));
      const recipient = await wait(() => {
        const matches = [...(modal()?.querySelectorAll(".PeerPickerItem") || [])].filter(row => visible(row) && row.querySelector(`[data-peer-id="${job.recipient_id}"]`) && new RegExp("@" + job.recipient + "(?![a-z0-9_])", "i").test(row.textContent));
        return matches.length === 1 ? matches[0] : null;
      });
      recipient.click();
      phase = "Проверка финального диалога";
      await wait(() => checkedConfirmation(job));
      await message(job, "prepared", { slug: job.slug, recipient_id: job.recipient_id, stars: job.stars });
      if (job.preview_only) return false;
      await wait(() => authorized, 30000);
      return true;
    } catch (error) { throw error; }
  }
  // Authorization polling is separate so wait() never treats a Promise as ready.
  let authorized = false;
  async function run(job) {
    authorized = false;
    const poll = setInterval(async () => {
      try {
        const state = await chrome.runtime.sendMessage({ type: "job-status", job_id: job.id, lease: job.lease });
        if (state?.state === "AUTHORIZED") authorized = true;
        if (["FAILED", "CANCELLED", "UNKNOWN"].includes(state?.state)) cancelled = true;
      } catch {}
    }, 1000);
    try {
      if (!await prepare(job)) return;
      if (!authorized || cancelled) return;
      const before = checkedConfirmation(job);
      if (!before) throw new Error("Подтверждение передачи изменилось");
      const permit = await message(job, "submit");
      const after = checkedConfirmation(job);
      if (!permit?.permit || cancelled || !after || after.button !== before.button) throw new Error("Передача не разрешена или диалог изменился");
      after.button.click();
      // Disappearing dialogs are not success. The server checks exact NFT ownership.
      await message(job, "done");
    } catch (error) {
      let detail = "";
      if (/меню|профиля/.test(phase)) {
        const items = [...document.querySelectorAll(".MenuItem, .menu-item, [role=menuitem]")];
        // Only navigation labels, never chat messages, account state or cookies.
        const profiles = items.filter(el => /profile|профиль/i.test(label(el) + plainText(el))).slice(0, 2);
        detail = " | " + JSON.stringify({ open_before: menuWasOpen, items: items.length, visible: items.filter(visible).length, profiles: profiles.map(el => ({ label: label(el).slice(0, 35), text: plainText(el).slice(0, 35), visible: visible(el), tag: el.tagName })) });
      }
      await message(job, "error", { error: (phase + ": " + String(error.message).slice(0, 90) + detail).slice(0, 300) }).catch(() => {});
    }
    finally { clearInterval(poll); active = false; authorized = false; }
  }
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message.type === "probe") {
      // WebA may retain an Auth element during transitions. Its presence is
      // not evidence that the user is logged out; inspect visible UI only.
      const app = [...document.querySelectorAll("#Main, #LeftColumn, #LeftColumn-main, #LeftMainHeader")].some(visible);
      const login = [...document.querySelectorAll("#auth-pages, .Auth")].some(el => visible(el) && !el.closest('[hidden], [aria-hidden="true"], [inert]') && Number(getComputedStyle(el).opacity) > 0);
      const inactive = visible(document.querySelector("#AppInactive"));
      const ready = app && !login;
      const diagnostic = !ready ? {
        doc: document.readyState, view: document.visibilityState,
        main: !!document.querySelector("#Main"), left: !!document.querySelector("#LeftColumn"),
        root: [...(document.getElementById("root")?.children || [])].slice(0, 4).map(el => el.tagName + "." + String(el.className).slice(0, 45)).join(","),
        vp: `${innerWidth}x${innerHeight}`, frames: document.querySelectorAll("iframe").length,
      } : undefined;
      respond({ ready: ready && !inactive, needsReload: inactive, page_state: inactive ? "inactive" : ready ? "ready" : login ? "login" : "loading", error: inactive ? "Telegram приостановил служебную вкладку. Восстанавливаем её в фоне" : ready ? "" : login && !app ? "Войдите в Telegram Web A в служебной вкладке" : "Ожидаем загрузку интерфейса Telegram Web", app_visible: app, login_visible: login, diagnostic });
    }
    if (message.type === "prepare") {
      if (active) { respond({ started: true }); return; }
      if (!/^[A-Za-z0-9]+-\d+$/.test(message.job?.slug) || !Number.isSafeInteger(message.job?.recipient_id)) { respond({ started: false }); return; }
      active = true; cancelled = false; respond({ started: true }); void run(message.job);
    }
  });
})();
