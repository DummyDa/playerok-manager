"use strict";
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const ton = (value) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 9 });
const integer = (value) => Number(value).toLocaleString("ru-RU");
let detailAnimationStop, hoverAnimationStop, animationGeneration = 0, gridAnimationObserver;
const gridAnimationPlayers = new Map();
let cardRevealObserver;
const revealedGifts = new Set();
const cardMotionQuery = matchMedia("(prefers-reduced-motion:reduce)");
let items = [], nextOffset = null, loading = false, requestSeq = 0, filterSeq = 0, browseSnapshot = null, browseFxRate = 0;
let currentGift = null, sourceStatus = null, searchTimer, priceTimer, toastTimer, controller, spotlightReady = true;
let paidChoice=null,paidChoiceLoading=true,paymentCodeOpen=false,paymentSessionRevision=0;
let recipientChoice=null,recipientBusy=false;
let regularCheckoutBusy=false;
const regularCheckoutLinks=new Map();

function renderListingTimer(){
  const box=$("#listing-timer");
  const funded=paidChoice && !usesRegularCheckout();
  const expires=funded?(paidChoice.state==="AWAITING_TOPUP" && paidChoice.selected?.id===currentGift?.id?paidChoice.expires_at:null):(currentGift?.checkout_url?currentGift.expires_at:null);
  box.hidden=!expires;
  if(!expires)return;
  const left=Math.max(0,Math.ceil(Number(expires)-Date.now()/1000));
  box.classList.toggle("is-urgent",left<=120);
  $("#listing-timer-value").textContent=`${String(Math.floor(left/60)).padStart(2,"0")}:${String(left%60).padStart(2,"0")}`;
  $("#listing-timer-label").textContent=left?"Лот активен ещё":"Время оплаты истекло";
  $("#listing-timer-hint").textContent=!left?"Не оплачивайте старый лот. Дождитесь проверки его статуса.":left<=120?"Осталось меньше двух минут — завершите оплату на Playerok":"Успейте оплатить на Playerok до окончания таймера";
  $("#listing-timer-progress").style.width=`${Math.min(100,left/900*100)}%`;
}

function renderRegularCheckout() {
  renderListingTimer();
  if(!currentGift)return;
  const button=$("#check-gift"),output=$("#check-result");
  if(regularCheckoutBusy){button.disabled=true;button.textContent="Создаём лот на Playerok…";return;}
  if(currentGift.checkout_url){
    const left=Math.max(0,Math.ceil(currentGift.expires_at-Date.now()/1000));
    button.disabled=!left;button.textContent=left?"Открыть лот на Playerok":"Время оплаты истекло";
    if(!left){output.textContent="Время оплаты истекло. Не оплачивайте старый лот.";return;}
    let link=output.querySelector("a");
    if(!link){link=document.createElement("a");link.target="_blank";link.rel="noopener";output.replaceChildren(link);}
    link.href=currentGift.checkout_url;
    link.textContent="Оплатить на Playerok ↗";
    output.classList.remove("error");return;
  }
  button.disabled=!!currentGift.unavailable;
  button.textContent=currentGift.unavailable?"Предложение недоступно":"Купить за "+rubPrice(currentGift.price_ton);
}
setInterval(()=>{
  if($("#gift-dialog").open)renderListingTimer();
  if(currentGift?.checkout_url && $("#gift-dialog").open && (!paidChoice || usesRegularCheckout()))renderRegularCheckout();
},1000);
function loadNearbyPage() {
  if (loading || nextOffset === null || $("#gift-dialog").open || document.hidden) return;
  const bounds = $("#load-sentinel").getBoundingClientRect();
  if (bounds.top <= innerHeight + 1200 && bounds.bottom >= 0) loadGifts(true, true);
}
if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) loadNearbyPage();
  }, { rootMargin: "0px 0px 1200px 0px" });
  observer.observe($("#load-sentinel"));
}
const initial = new URLSearchParams(location.search);
const state = { q: initial.get("q") || "", collection: initial.get("collection") || "", model: initial.get("model") || "", backdrop: initial.get("backdrop") || "", symbol: initial.get("symbol") || "", sort: initial.get("sort") || "price_asc", min_price: initial.get("min_price") || "", max_price: initial.get("max_price") || "" };
let legacyPriceUnits = initial.get("currency") !== "rub" && Boolean(state.min_price || state.max_price);
if (!state.collection) state.model = "";
window.catalogFilters = { data: null, state: { ...state }, loading: true, error: "" };
function publishFilters(data = window.catalogFilters.data, loading = window.catalogFilters.loading, error = "") {
  window.catalogFilters = { data, state: { ...state }, loading, error, fx: window.catalogCurrency };
  window.dispatchEvent(new CustomEvent("catalog-filters-updated", { detail: window.catalogFilters }));
}
if (!["newest", "price_asc", "price_desc", "number", "number_desc"].includes(state.sort)) state.sort = "price_asc";
$("#search").value = state.q;
$("#sort").value = state.sort;
$("#min-price").value = state.min_price;
$("#max-price").value = state.max_price;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  function publicMessages(value){
    if(!value || typeof value!=="object")return;
    for(const [key,item] of Object.entries(value)){
      if(["detail","error","notice"].includes(key) && typeof item==="string")value[key]=item.replace(/MRKT/gi,"каталог");
      else if(item && typeof item==="object")publicMessages(item);
    }
  }
  publicMessages(data);
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось загрузить данные. Попробуй ещё раз.");
  return data;
}
function announce(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 2600);
}
function rubRate() {
  const fx = sourceStatus?.fx;
  const rate = Number(fx?.rate), age = Date.now() / 1000 - Number(fx?.fetched_at);
  return Number.isFinite(rate) && rate > 0 && age >= -60 && age < 900 ? rate : 0;
}
function rubPrice(value) {
  if(paidChoice && !["DELETED","EXHAUSTED"].includes(paidChoice.state))return "Получить";
  const p = sourceStatus?.pricing;
  if (!p?.ready || !rubRate()) return "Цена недоступна";
  return `${Math.ceil(Number(value) * Number(p.ton_multiplier) + Number(p.fixed_rub)).toLocaleString("ru-RU")} ₽`;
}
function updateCurrency() {
  const rate = rubRate();
  const changed = window.catalogCurrency?.rate !== rate || JSON.stringify(window.catalogCurrency?.pricing) !== JSON.stringify(sourceStatus?.pricing);
  window.catalogCurrency = { pricing: sourceStatus?.pricing, rate, fetched_at: sourceStatus?.fx?.fetched_at };
  if (legacyPriceUnits && rate) {
    ["min_price", "max_price"].forEach(key => {
      if (state[key]) state[key] = String(Math.round(Number(state[key]) * rate));
    });
    $("#min-price").value = state.min_price; $("#max-price").value = state.max_price;
    legacyPriceUnits = false;
  }
  ["min-price", "max-price"].forEach(id => { $("#" + id).disabled = !rate || !sourceStatus?.pricing?.ready; });
  if (changed) {
    document.querySelectorAll("[data-ton-price]").forEach(el => { el.textContent = rubPrice(el.dataset.tonPrice); });
  }
  if (currentGift && !currentGift.checkout_url && !$("#check-gift").disabled) $("#check-gift").textContent = "Купить за " + rubPrice(currentGift.price_ton);
  renderPaidDetail();
  publishFilters();
}
function age(timestamp) {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - timestamp));
  if (seconds < 60) return "только что";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч назад`;
  return `${Math.floor(seconds / 86400)} дн назад`;
}
function stale(gift) { return Date.now() / 1000 - gift.checked_at > (sourceStatus?.stale_after || 180); }
function art(gift, eager = false) {
  const pattern = "";
  return `<img class="gift-image" src="${esc(gift.image || '/icon.svg')}" alt="${esc(gift.model)}" loading="${eager ? 'eager' : 'lazy'}" decoding="async" width="240" height="240">${pattern}`;
}
function renderCards(fromIndex = 0) {
  if (!fromIndex) {
    gridAnimationObserver?.disconnect();
    cardRevealObserver?.disconnect();
    gridAnimationPlayers.forEach(stop => stop?.());gridAnimationPlayers.clear();
  }
  const markup = items.slice(fromIndex).map((gift, index) => `<article class="gift-card">
    <button class="card-open" data-id="${esc(gift.id)}" data-slug="${esc(gift.slug || "")}" aria-label="Открыть ${esc(gift.collection_label || gift.collection)} номер ${gift.number}">
      <div class="gift-art" style="--c1:${esc(gift.center)};--c2:${esc(gift.edge)}"></div>
      <div class="gift-body"><h3>${esc(gift.collection_label || gift.collection)}</h3><p class="gift-model">#${integer(gift.number)}</p>
        <span class="gift-freshness${stale(gift) ? ' stale' : ''}">${stale(gift) ? 'Обновление ожидается' : 'Проверено ' + age(gift.checked_at)}</span>
      </div></button><button type="button" class="card-buy" data-id="${esc(gift.id)}" aria-label="Купить ${esc(gift.collection_label || gift.collection)} номер ${gift.number}"><span class="gift-price" data-ton-price="${esc(gift.price_ton)}">${rubPrice(gift.price_ton)}</span></button>
    </article>`).join("");
  if (fromIndex) $("#grid").insertAdjacentHTML("beforeend", markup);
  else $("#grid").innerHTML = markup;
  observeGiftAnimations();
  observeCardReveals();
  $("#empty").hidden = items.length !== 0;
  $("#empty-text").textContent = sourceStatus?.count ? "Попробуй другой запрос или убери часть фильтров." : "Собираем первые предложения. Каталог появится автоматически.";
}
function syncUrl() {
  const query = new URLSearchParams();
  Object.entries(state).forEach(([key, value]) => { if (value && !(key === "sort" && value === "price_asc")) query.set(key, value); });
  if (state.min_price || state.max_price) query.set("currency", "rub");
  history.replaceState(null, "", location.pathname + (query.size ? "?" + query : ""));
}
function clearLoadingCards() { document.querySelectorAll('#grid .loading-card').forEach(card=>card.remove()); }
function showLoadingCards(append) {
  clearLoadingCards();
  if(!append) {
    gridAnimationObserver?.disconnect();cardRevealObserver?.disconnect();
    gridAnimationPlayers.forEach(stop=>stop?.());gridAnimationPlayers.clear();
    items=[];nextOffset=null;browseSnapshot=null;
    $("#grid").replaceChildren();
    $("#empty").hidden=true;$("#end-of-results").hidden=true;$("#load-more").hidden=true;
    $("#results").textContent='';
  }
  const count=append?6:12;
  $("#grid").insertAdjacentHTML('beforeend',Array.from({length:count},()=>'<div class="skeleton loading-card" aria-hidden="true"></div>').join(''));
}
async function loadGifts(append = false, quiet = false) {
  if (append && (loading || nextOffset === null)) return;
  const seq = ++requestSeq;
  if (controller) controller.abort();
  controller = new AbortController();
  loading = true;
  let succeeded = false;
  $("#grid").setAttribute("aria-busy", "true");
  $("#load-more").disabled = true;
  $("#load-more").firstChild.textContent = "Загружаем… ";
  if (!quiet || append) showLoadingCards(append);
  $("#catalog-error").hidden = true;
  const query = new URLSearchParams();
  if (!append) browseFxRate = rubRate();
  Object.entries(state).forEach(([key, value]) => {
    if (!value) return;
    const price = key === "min_price" || key === "max_price";
    if (!price || browseFxRate) query.set(key, price ? Math.max(0, (Number(value) - Number(sourceStatus?.pricing?.fixed_rub || 0)) / Number(sourceStatus?.pricing?.ton_multiplier || browseFxRate)) : value);
  });
  query.set("offset", append ? nextOffset : 0);
  query.set("limit", 30);
  if (append && browseSnapshot) query.set("snapshot", browseSnapshot);
  try {
    if ((state.min_price || state.max_price) && (!browseFxRate || !sourceStatus?.pricing?.ready)) throw new Error("Для фильтра по цене нужен актуальный курс рубля.");
    let data;
    for (let attempt = 0; attempt < 90; attempt++) {
      data = await api("/api/browse?" + query, { signal: controller.signal });
      if (seq !== requestSeq) return;
      if (!data.pending) break;

      $("#empty").hidden = true;
      if (data.snapshot) query.set("snapshot", data.snapshot);
      await new Promise(resolve => setTimeout(resolve, 500));
      if (controller.signal.aborted || seq !== requestSeq) return;
    }
    if (data.pending) throw new Error("Каталог отвечает медленно. Попробуйте загрузить ещё раз.");
    if (seq !== requestSeq) return;
    clearLoadingCards();
    const oldLength = append ? items.length : 0;
    items = append ? [...items, ...data.items] : data.items;
    // A collection may change between pages. Never render duplicate gift IDs.
    items = [...new Map(items.map((gift) => [gift.id, gift])).values()];
    nextOffset = data.next_offset;
    browseSnapshot = data.snapshot || null;
    renderCards(oldLength);
    if (!append && (state.model || state.backdrop || state.symbol)) loadFilters();
    $("#results").textContent = `${data.total_is_upper_bound ? 'До ' : ''}${integer(data.total)} предложений`;
    $("#load-more").hidden = nextOffset === null;
    $("#load-sentinel").hidden = nextOffset === null;
    $("#end-of-results").hidden = nextOffset !== null || !items.length;
    succeeded = true;
    syncUrl();
  } catch (error) {
    if (seq !== requestSeq || error.name === "AbortError") return;
    clearLoadingCards();
    $("#catalog-error").textContent = "Не удалось загрузить каталог. " + error.message;
    $("#catalog-error").hidden = false;
    $("#load-more").hidden = false;
    $("#results").textContent = "Ошибка загрузки";
    if (!items.length) $("#grid").innerHTML = "";
  } finally {
    if (seq === requestSeq) {
      loading = false;
      $("#grid").setAttribute("aria-busy", "false");
      $("#load-more").disabled = false;
      $("#load-more").firstChild.textContent = "Показать ещё ";
      // Continue filling a short viewport; failed requests wait for a manual retry.
      if (succeeded && "IntersectionObserver" in window) requestAnimationFrame(loadNearbyPage);
    }
  }
}
const filterLabels = { collection: "Все коллекции", model: "Любая модель", backdrop: "Любой фон", symbol: "Любой узор" };
async function loadFilters() {
  const seq = ++filterSeq;
  publishFilters(undefined, true);
  try {
    const data = await api("/api/filters?" + new URLSearchParams({ collection: state.collection }));
    if (seq !== filterSeq) return;
    Object.keys(filterLabels).forEach((key) => {
      const values = data[key] || [];
      const missing = state[key] && !values.some((v) => v.name === state[key]);
      $("#" + key).innerHTML = `<option value="">${filterLabels[key]}</option>` + (missing ? `<option value="${esc(state[key])}">${esc(state[key])}</option>` : "") + values.map((v) => `<option value="${esc(v.name)}">${esc(v.title || v.name)}${v.count == null ? '' : ' (' + v.count + ')'}</option>`).join("");
      $("#" + key).value = state[key];
    });
    $("#collections").innerHTML = `<button class="collection-chip${state.collection ? '' : ' active'}" data-collection="">Все коллекции</button>` + data.collection.map((c) => `<button class="collection-chip${state.collection === c.name ? ' active' : ''}" data-collection="${esc(c.name)}">${c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy" width="29" height="29">` : ''}${esc(c.title || c.name)}</button>`).join("");
    filterCollectionList();
    publishFilters(data, false);
    if (!spotlightReady && data.collection.length >= 3) renderSpotlight(data.collection);
  } catch { if (seq === filterSeq) publishFilters(undefined, false, "Не удалось обновить варианты"); }
}
function renderSpotlight(collections) {
  const preferred = ["Toy Bear", "Plush Pepe", "Heart Locket"];
  let picks = preferred.map((name) => collections.find((c) => c.name === name)).filter(Boolean);
  for (const c of collections) if (picks.length < 3 && !picks.includes(c)) picks.push(c);
  window.giftroomHeroCards = picks;
  window.dispatchEvent(new CustomEvent("hero-cards-ready", { detail: picks }));
  spotlightReady = preferred.every((name) => collections.some((c) => c.name === name));
}
async function refreshStatus() {
  try {
    sourceStatus = await api("/api/status");
    updateCurrency();
    $("#catalog-total").textContent = integer(sourceStatus.count);
    const staleSnapshot = sourceStatus.updated_at && Date.now() / 1000 - sourceStatus.updated_at > sourceStatus.stale_after;
    $("#source-dot").classList.toggle("warning", Boolean(sourceStatus.error || staleSnapshot));
    $("#source-title").textContent = sourceStatus.error ? "Обновление приостановлено" : sourceStatus.count ? "Предложения каталога" : "Собираем каталог";
    $("#source-sub").textContent = (sourceStatus.error || sourceStatus.pricing?.error) ? (sourceStatus.error || sourceStatus.pricing?.error) : `${sourceStatus.collections} коллекций · страницы подгружаются по мере просмотра`;
    $("#source-time").textContent = sourceStatus.updated_at ? "Проверено " + age(sourceStatus.updated_at) : "Первая синхронизация…";
  } catch {
    updateCurrency();
    $("#source-title").textContent = "Нет связи с сервером";
    $("#source-dot").classList.add("warning");
    $("#source-sub").textContent = "Сохранённый экран может быть неактуален";
  }
}
async function startNftAnimation(target, id, detail = false) {
  const generation = ++animationGeneration;
  const module = await import('/nft-animation.js');
  if (generation !== animationGeneration || !target.isConnected) return;
  const stop = module.mountAnimation(target, id);
  if (detail) detailAnimationStop = stop; else hoverAnimationStop = stop;
}
function showDetail(gift) {
  animationGeneration++; hoverAnimationStop?.(); detailAnimationStop?.();
  const saved=regularCheckoutLinks.get(gift.id);
  currentGift = saved?.expires_at>Date.now()/1000?{...gift,...saved}:gift;
  $("#detail-art").style.setProperty("--c1", gift.center);
  $("#detail-art").style.setProperty("--c2", gift.edge);
  $("#detail-art").dataset.slug = gift.slug || '';
  const previewSlug=String(gift.slug || '').toLowerCase();
  $("#detail-art").innerHTML = /^[a-z0-9]+-\d+$/.test(previewSlug)
    ? `<img class="nft-detail-preview" alt="${esc(gift.model)}" src="https://nft.fragment.com/gift/${previewSlug}.medium.jpg">` : art(gift, true);
  startNftAnimation($("#detail-art"), gift.id, true).catch(()=>{});
  $("#detail-title").textContent = gift.collection_label || gift.collection;
  $("#detail-model").textContent = `#${integer(gift.number)}`;
  $("#detail-traits").innerHTML = [["Модель", gift.model, gift.rarity], ["Узор", gift.symbol, null], ["Фон", gift.backdrop, null]].map(([key, value, rarity]) => `<p><span>${key}</span><strong>${esc(value || 'Не указан')}${rarity != null ? `<small>${ton(rarity / 10)}%</small>` : ''}</strong></p>`).join("");
  $("#detail-age").textContent = `Проверено ${age(gift.checked_at)}${stale(gift) ? ' · Данные требуют обновления' : ''}`;
  $("#check-result").textContent = "";
  $("#check-result").classList.remove("error");
  $("#check-gift").disabled = !!gift.unavailable;
  $("#check-gift").textContent = gift.unavailable ? "Предложение недоступно" : "Купить за " + rubPrice(gift.price_ton);
  renderPaidDetail();
  if (!$("#gift-dialog").open) { $("#gift-dialog").classList.remove("is-closing"); $("#gift-dialog").showModal(); }
}
function updateCheckedGift(gift) {
  const index=items.findIndex(item=>item.id===gift.id);
  if(index>=0)items[index]={...items[index],...gift};
  const button=[...document.querySelectorAll('#grid .card-open')].find(el=>el.dataset.id===gift.id);
  const card=button?.closest('.gift-card');
  if(!card)return;
  card.dataset.unavailable=String(!!gift.unavailable);
  const buy=card.querySelector('.card-buy');
  buy.disabled=!!gift.unavailable;
  buy.innerHTML=gift.unavailable?'Недоступно':`<span class="gift-price" data-ton-price="${esc(gift.price_ton)}">${rubPrice(gift.price_ton)}</span>`;
  const freshness=card.querySelector('.gift-freshness');
  if(freshness){freshness.textContent=gift.unavailable?'Предложение недоступно':'Проверено только что';freshness.classList.remove('stale');}
}
async function verifyGift() {
  if (!currentGift) return;
  if (paidChoiceLoading) return;
  if (paidChoice && !usesRegularCheckout()) {await verifyPaidGift();return;}
  if (currentGift.checkout_url && currentGift.expires_at > Date.now()/1000) {
    window.open(currentGift.checkout_url,"_blank","noopener"); return;
  }
  const selectedId=currentGift.id;
  const shown=currentGift.price_rub ?? Math.ceil(Number(currentGift.price_ton)*Number(sourceStatus?.pricing?.ton_multiplier)+Number(sourceStatus?.pricing?.fixed_rub));
  if (!sourceStatus?.pricing?.ready || !Number.isFinite(shown)) {announce("Цена пока недоступна");return;}
  const button=$("#check-gift"), output=$("#check-result");
  regularCheckoutBusy=true;
  button.disabled=true;button.textContent="Создаём лот на Playerok…";output.textContent="";output.classList.remove("error");
  try {
    const request=await api("/api/checkout/"+encodeURIComponent(selectedId),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({price_rub:shown})});
    let result;
    for(let i=0;i<180;i++) {
      result=await api("/api/checkout/"+encodeURIComponent(request.id));
      if(["READY","PRICE_CHANGED","FAILED","UNKNOWN","EXPIRED","PAID"].includes(result.state))break;
      if(result.state==="QUEUED" && result.notice){output.textContent=result.notice;button.textContent="Ожидаем проверку наличия…";}
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
    if(currentGift?.id!==selectedId)return;
    if(result.state==="PRICE_CHANGED" && result.updated_gift) {
      sourceStatus=await api("/api/status");updateCurrency();
      updateCheckedGift(result.updated_gift);showDetail(result.updated_gift);
      $("#check-result").textContent=`Цена изменилась: теперь ${Number(result.price_rub).toLocaleString("ru-RU")} ₽. Нажмите «Купить» ещё раз, если цена подходит.`;
      button.textContent=`Купить за ${Number(result.price_rub).toLocaleString("ru-RU")} ₽`;
      return;
    }
    if(result.state!=="READY")throw new Error(result.error || (result.state==="UNKNOWN"?"Создание требует проверки в основной панели. Повторного создания не будет.":"Лот ещё создаётся или недоступен. Проверьте позже."));
    const paymentUrl=new URL(result.url);
    if(paymentUrl.protocol!=="https:" || paymentUrl.hostname!=="playerok.com")throw new Error("Не подтверждён адрес лота Playerok");
    const saved={checkout_url:paymentUrl.href,expires_at:result.expires_at};
    regularCheckoutLinks.set(selectedId,saved);
    currentGift={...currentGift,...saved};
    // Open only the finished listing; keep the card link if popups are blocked.
    try {window.open(paymentUrl.href,"_blank","noopener");} catch {}
  } catch(error) {
    if(currentGift?.id===selectedId){output.textContent=error.message;output.classList.add("error");button.textContent="Повторить";}
  } finally {
    regularCheckoutBusy=false;
    if(currentGift?.id===selectedId){if(currentGift.checkout_url)renderRegularCheckout();else button.disabled=false;}
  }
}
function reset() {
  clearTimeout(priceTimer);
  legacyPriceUnits = false;
  Object.keys(state).forEach((key) => { state[key] = key === "sort" ? "price_asc" : ""; });
  $("#search").value = "";
  $("#collection-search").value = "";
  $("#sort").value = "price_asc";
  $("#min-price").value = $("#max-price").value = "";
  $("#filter-error").textContent = "";
  loadFilters();
  loadGifts();
}
function selectCollection(value) {
  const read = raw => { try { return raw ? raw.startsWith('[') ? JSON.parse(raw) : [raw] : []; } catch { return []; } };
  const oldOwners = read(state.collection), owners = read(value);
  const models = read(state.model).flatMap(entry => {
    const pair = entry.includes('::model::') ? entry.split('::model::') : oldOwners.length === 1 ? [oldOwners[0],entry] : null;
    if (!pair || !owners.includes(pair[0])) return [];
    return [owners.length === 1 ? pair[1] : pair.join('::model::')];
  });
  const selected = [...new Set(models)].sort();
  state.model = selected.length === 1 ? selected[0] : selected.length ? JSON.stringify(selected) : '';
  state.collection = value;
  const previous = window.catalogFilters.data;
  publishFilters(previous ? { collection: previous.collection, model: [], backdrop: [], symbol: [] } : null, true);
  loadFilters();
  loadGifts();
}
function applyPriceFilters() {
  clearTimeout(priceTimer);
  let min = $("#min-price").value, max = $("#max-price").value;
  const inputs = [$("#min-price"), $("#max-price")];
  if (inputs.some(input => input.validity.badInput) ||
      [min, max].some(value => value && (!Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 1e9)) ||
      (min && max && Number(min) > Number(max))) {
    $("#filter-error").textContent = "Проверь диапазон: от 0 до 1 млрд ₽, цена от должна быть меньше цены до.";
    return;
  }
  if (min) min = String(Math.round(Number(min)));
  if (max) max = String(Math.round(Number(max)));
  $("#min-price").value = min; $("#max-price").value = max;
  if (!rubRate()) { $("#filter-error").textContent = "Дождись обновления курса рубля."; return; }
  $("#filter-error").textContent = "";
  if (state.min_price === min && state.max_price === max) return;
  state.min_price = min;
  state.max_price = max;
  publishFilters();
  loadGifts();
}
$("#filters").addEventListener("submit", event => { event.preventDefault(); applyPriceFilters(); });
["min-price", "max-price"].forEach(id => {
  $("#" + id).addEventListener("input", () => { clearTimeout(priceTimer); priceTimer = setTimeout(applyPriceFilters, 350); });
  $("#" + id).addEventListener("change", applyPriceFilters);
});
$("#collection").addEventListener("change", (e) => selectCollection(e.target.value));
["model", "backdrop", "symbol"].forEach((key) => $("#" + key).addEventListener("change", (e) => { state[key] = e.target.value; loadGifts(); }));
$("#collections").addEventListener("click", (event) => { const button = event.target.closest("[data-collection]"); if (button) selectCollection(button.dataset.collection); });
$("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.q = $("#search").value; loadGifts(); }, 250); });
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; loadGifts(); });
$("#load-more").addEventListener("click", () => loadGifts(nextOffset !== null));
$("#reset").addEventListener("click", reset);
$("#empty-reset").addEventListener("click", reset);
$("#grid").addEventListener("click", (event) => {
  const button = event.target.closest(".card-open, .card-buy");
  const gift = button && items.find((g) => g.id === button.dataset.id);
  if (gift) showDetail(gift);
});
$("#close-dialog").addEventListener("click", closeGiftDialog);
$("#gift-dialog").addEventListener("cancel", event => { event.preventDefault(); closeGiftDialog(); });
$("#gift-dialog").addEventListener("click", (event) => { if (event.target === $("#gift-dialog")) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeGiftDialog(); } });
$("#gift-dialog").addEventListener("close", () => { currentGift = null; animationGeneration++; detailAnimationStop?.(); });
$("#check-gift").addEventListener("click", verifyGift);
document.addEventListener("keydown", (event) => { if (event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) && !$("#gift-dialog").open) { event.preventDefault(); $("#search").focus(); } });
document.addEventListener("error", (event) => { if (event.target.tagName === "IMG" && !event.target.dataset.failed) { event.target.dataset.failed = "1"; event.target.src = "/icon.svg"; } }, true);
showLoadingCards(false);
async function initialLoad() { await refreshStatus(); await Promise.all([loadGifts(), loadFilters()]); }
initialLoad();
setInterval(async () => {
  if (document.hidden) return;
  await refreshStatus();
  if (!loading && !$("#gift-dialog").open && items.length <= 30 && document.activeElement.tagName !== "SELECT") {
    await Promise.all([loadGifts(false, true), loadFilters()]);
  }
}, 15000);

// Reference catalogue controls.
function filterCollectionList() {
  const text = $("#collection-search").value.trim().toLocaleLowerCase();
  document.querySelectorAll("#collections .collection-chip").forEach(button => {
    button.hidden = !!text && !button.textContent.toLocaleLowerCase().includes(text);
  });
}
$("#collection-search").addEventListener("input", filterCollectionList);
function setView(list) {
  $("#grid").classList.toggle("list-view", list);
  $("#view-grid").setAttribute("aria-pressed", String(!list));
  $("#view-grid").innerHTML = list
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/></svg>';
  $("#view-list").setAttribute("aria-pressed", String(list));
}
$("#view-grid").addEventListener("click", () => { const list = !$("#grid").classList.contains("list-view"); setView(list); $("#view-grid").setAttribute("aria-label", list ? "Показать сетку" : "Показать список"); });
$("#view-list").addEventListener("click", () => setView(true));
$("#header-search").addEventListener("click", () => {
  $("#search").scrollIntoView({behavior:cardMotionQuery.matches?'instant':'smooth', block:'center'});
  $("#search").focus({preventScroll:true});
});


// Keep the header's layout space stable while its fixed panel contracts.
const floatingHeader = document.querySelector('.header');
let headerFrame = 0;
function updateFloatingHeader() {
  headerFrame = 0;
  const compact = floatingHeader.classList.contains('is-scrolled');
  // A small dead zone prevents flicker around the scroll threshold.
  floatingHeader.classList.toggle('is-scrolled', window.scrollY > (compact ? 16 : 40));
}
window.addEventListener('scroll', () => {
  if (!headerFrame) headerFrame = requestAnimationFrame(updateFloatingHeader);
}, { passive: true });
window.addEventListener('pageshow', updateFloatingHeader);
updateFloatingHeader();

// React filter panels commit through the existing catalogue controls.
window.addEventListener("catalog-filter-apply", event => {
  const { key, value } = event.detail || {};
  if (!["collection", "model", "backdrop", "symbol", "sort"].includes(key) || typeof value !== "string") return;
  if (key === "model" && !state.collection) return;
  const select = document.getElementById(key);
  if (value && ![...select.options].some(option => option.value === value)) select.add(new Option(value, value));
  select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
});
["collection", "model", "backdrop", "symbol", "sort"].forEach(key => {
  document.getElementById(key).addEventListener("change", () => publishFilters());
});


function observeGiftAnimations() {
  if (!gridAnimationObserver) gridAnimationObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      const target = entry.target;
      if (!entry.isIntersecting) {
        gridAnimationPlayers.get(target)?.();gridAnimationPlayers.delete(target);return;
      }
      if (gridAnimationPlayers.has(target)) return;
      gridAnimationPlayers.set(target,null);
      const layer=document.createElement('div');layer.className='nft-animation';layer.setAttribute('aria-hidden','true');
      const img=new Image();img.className='nft-poster';img.alt='';img.decoding='async';
      const card=target.closest('.card-open');
      const id=card.dataset.id,slug=String(card.dataset.slug || '').toLowerCase();
      img.onload=()=>{target.classList.add('nft-ready');target.classList.remove('nft-loading');};
      let fallback=false;
      img.onerror=()=>{if(!fallback){fallback=true;img.src='/api/gift-image/'+encodeURIComponent(id);return;}target.classList.add('nft-failed');target.classList.remove('nft-loading');layer.remove();};
      target.classList.add('nft-loading');layer.append(img);target.append(layer);
      img.src=/^[a-z0-9]+-\d+$/.test(slug)?`https://nft.fragment.com/gift/${slug}.medium.jpg`:'/api/gift-image/'+encodeURIComponent(id);
      gridAnimationPlayers.set(target,()=>{img.onload=img.onerror=null;layer.remove();target.classList.remove('nft-ready','nft-loading');});
    });
  }, {rootMargin:'500px'});
  document.querySelectorAll('#grid .gift-art').forEach(target=>gridAnimationObserver.observe(target));
}

let nftWarmTimer;
function warmGiftAnimation(event) {
  const card=event.target.closest('.card-open');
  if(!card || card.contains(event.relatedTarget))return;
  clearTimeout(nftWarmTimer);
  nftWarmTimer=setTimeout(()=>import('/nft-animation.js').then(module=>module.preloadAnimation(card.dataset.id,card.dataset.slug)).catch(()=>{}),100);
}
$("#grid").addEventListener('pointerover',warmGiftAnimation);
$("#grid").addEventListener('focusin',warmGiftAnimation);

function closeGiftDialog() {
  const dialog=$("#gift-dialog");
  if (!dialog.open || dialog.classList.contains('is-closing'))return;
  if(cardMotionQuery.matches){dialog.close();return;}
  dialog.classList.add('is-closing');
  const animation=dialog.animate([{opacity:1,transform:'translateY(0) scale(1)'},{opacity:0,transform:'translateY(10px) scale(.97)'}],{duration:160,easing:'ease-in',fill:'forwards'});
  animation.finished.catch(()=>{}).then(()=>{dialog.close();animation.cancel();dialog.classList.remove('is-closing')});
}
function observeCardReveals() {
  if(!cardRevealObserver) cardRevealObserver=new IntersectionObserver(entries=>{
    let order=0;
    entries.forEach(entry=>{
      if(!entry.isIntersecting)return;
      const card=entry.target,id=card.querySelector('.card-open').dataset.id;
      cardRevealObserver.unobserve(card);card.classList.remove('card-reveal-pending');
      if(revealedGifts.has(id))return;
      revealedGifts.add(id);
      while(revealedGifts.size>600)revealedGifts.delete(revealedGifts.values().next().value);
      if(!cardMotionQuery.matches) card.animate([
        {opacity:0,transform:'translateY(18px) scale(.985)'},
        {opacity:1,transform:'translateY(0) scale(1)'}
      ],{duration:420,delay:Math.min(order++*35,140),easing:'cubic-bezier(.22,1,.36,1)',fill:'backwards'});
    });
  },{threshold:.04});
  document.querySelectorAll('#grid .gift-card:not([data-reveal-observed])').forEach(card=>{
    card.dataset.revealObserved='true';
    if(cardMotionQuery.matches || revealedGifts.has(card.querySelector('.card-open').dataset.id))return;
    card.classList.add('card-reveal-pending');cardRevealObserver.observe(card);
  });
}
cardMotionQuery.addEventListener('change',()=>{
  if(!cardMotionQuery.matches)return;
  document.querySelectorAll('.card-reveal-pending').forEach(card=>card.classList.remove('card-reveal-pending'));
  document.querySelectorAll('#grid .gift-card,#gift-dialog').forEach(el=>el.getAnimations().forEach(animation=>animation.cancel()));
});


function giftSaleRub(gift) {
  const p=sourceStatus?.pricing;
  return p?.ready ? Math.ceil(Number(gift.price_ton)*Number(p.ton_multiplier)+Number(p.fixed_rub)) : NaN;
}
function usesRegularCheckout(){return (paidChoice?.state==="EXHAUSTED" || (paidChoice?.closed_after_delivery && paidChoice.selected?.id!==currentGift?.id)) && Number(paidChoice.balance_rub ?? paidChoice.paid_rub)<=0;}
function realCredit(value){if(value.test!==false)throw new Error("Тестовые коды отключены. Создайте новый код в панели каталога.");return value;}
function renderPaidDetail() {
  renderListingTimer();
  if(!currentGift)return;
  const panel=$("#detail-payment"),button=$("#check-gift");
  panel.hidden=!paidChoice;
  if(paidChoiceLoading){button.disabled=true;button.textContent="Проверяем оплату…";return;}
  if(!paidChoice){renderRegularCheckout();return;}
  const selected=paidChoice.selected?.id===currentGift.id;
  const available=Number(paidChoice.balance_rub ?? paidChoice.paid_rub);
  const regular=usesRegularCheckout();
  const price=giftSaleRub(currentGift),due=selected && paidChoice.state!=="AWAITING_SELECTION" ? Number(paidChoice.topup_rub||0) : price-available<=20?0:Math.max(90,price-available);
  const shown=!regular && selected && paidChoice.state!=="AWAITING_SELECTION" ? paidChoice.selected_price : price;
  panel.innerHTML=`Баланс заказа: <strong>${integer(available)} ₽</strong>${Number(paidChoice.reserved_rub)>0?`<br>Зарезервировано: <strong>${integer(paidChoice.reserved_rub)} ₽</strong>`:""}${regular?`<br>Цена подарка: <strong>${Number.isFinite(Number(shown))?integer(shown)+" ₽":"уточняется"}</strong>`:due>0?`<br>Доплата: <strong>${integer(due)} ₽</strong>`:"<br><strong>Подарок доступен без доплаты</strong>"}${regular?"":`<br>Получатель: <strong>@${esc(paidChoice.recipient)}</strong>`}`;
  if(regular){
    if(button.dataset.checkoutMode!=="regular")$("#check-result").textContent="";
    button.dataset.checkoutMode="regular";
    renderRegularCheckout();
    $(".detail-disclaimer").textContent="Лот Playerok создаётся на 15 минут.";
    return;
  }
  button.dataset.checkoutMode="funded";
  button.disabled=!!currentGift.unavailable || !Number.isFinite(price);
  $(".detail-disclaimer").textContent=sourceStatus?.pricing?.manual_delivery?"После оплаты подарок передаст продавец. По вопросам выдачи напишите продавцу в чате Playerok.":"Закупка и передача выполняются автоматически после подтверждения оплаты. При возникновении вопросов напишите продавцу в чате Playerok.";
  if(paidChoice.state==="DELETED") {
    button.disabled=true;button.textContent=paidChoice.closed_after_delivery?"Подарок получен":"Код недействителен";
    $("#check-result").textContent=paidChoice.closed_after_delivery?"Передача подтверждена. Код использован и закрыт. Выйдите из заказа для новой покупки.":"Код удалён владельцем. Выйдите из заказа, чтобы применить другой код.";
  } else if((paidChoice.purchased_ids || []).includes(currentGift.id)) {
    button.disabled=true;button.textContent="Подарок уже получен";
    $("#check-result").textContent=`Передача подарка подтверждена. На балансе заказа осталось ${integer(available)} ₽.`;
  } else if(paidChoice.state==="PREPARING"){
    button.disabled=true;button.textContent="Создаём лот доплаты…";
    $("#check-result").textContent="Подарок и сумма зафиксированы. Ссылка на оплату появится здесь.";
  } else if(paidChoice.state==="EXECUTING"){
    button.disabled=true;
    const stages={AWAITING_MANUAL_DELIVERY:"Подарок готов. Ожидаем выдачи продавцом.",PURCHASING:"Покупаем подарок…",PURCHASED:"Выводим NFT в Telegram…",WITHDRAWN:sourceStatus?.pricing?.manual_delivery?"Готовим подарок к выдаче продавцом…":"Передаём подарок…",TRANSFERRED:"Проверяем передачу…",AWAITING_ENABLE:"Ожидание разрешения выдачи"};
    button.textContent=stages[paidChoice.delivery_stage] || "Проверяем и выдаём подарок…";
    $("#check-result").textContent=paidChoice.delivery_stage==="AWAITING_MANUAL_DELIVERY"?"Продавец передаст подарок вашему Telegram-получателю. Повторно оплачивать заказ не нужно.":paidChoice.delivery_reason || "Баланс зарезервирован. Не повторяйте покупку — заказ уже выполняется.";
  } else if(paidChoice.state==="REVIEW"){
    button.disabled=true;button.textContent="Заказ проверяется";
    $("#check-result").textContent=paidChoice.notice || "Заказ проверяется. Повторно оплачивать не нужно. При необходимости свяжитесь с продавцом в чате Playerok.";
  } else if(paidChoice.state==="AWAITING_TOPUP") {
    const seconds=Math.max(0,Math.ceil(paidChoice.expires_at-Date.now()/1000));
    button.disabled=!selected || !paidChoice.payment_url || !seconds;
    button.textContent=!selected?"Другой подарок уже выбран":seconds?`Доплатить ${integer(paidChoice.topup_rub)} ₽ на Playerok`:"Проверяем статус оплаты…";
    $("#check-result").textContent=seconds?`Оплатите доплату на Playerok. Осталось ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,"0")}. После подтверждения оплаты начнётся выдача.`:"Не оплачивайте старый лот. Ждём проверки оплаты и снятия предложения.";
  } else {button.textContent=due>0?`Доплатить ${integer(due)} ₽`:"Получить подарок";if(paidChoice.notice)$("#check-result").textContent=paidChoice.notice;}
}
function renderPaidChoice() {
  if(paidChoice?.updated_gift && currentGift?.id===paidChoice.updated_gift.id && currentGift.checked_at!==paidChoice.updated_gift.checked_at){currentGift={...currentGift,...paidChoice.updated_gift};updateCheckedGift(currentGift);}
  $("#paid-choice").classList.toggle("is-active",!!paidChoice);
  $("#paid-choice").classList.toggle("is-editing",!!paidChoice && paymentCodeOpen);
  $("#payment-code-form").hidden=!!paidChoice && !paymentCodeOpen;
  $("#payment-code-cancel").hidden=!paidChoice;
  $("#leave-payment").hidden=!paidChoice;
  const available=Number(paidChoice?.balance_rub ?? paidChoice?.paid_rub ?? 0);
  const summary=paidChoice
    ? `<div class="payment-wallet" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M19 7V5a2 2 0 0 0-2-2H6a3 3 0 0 0 0 6h13v11H6a3 3 0 0 1-3-3V6m16 6h-5v5h5m-2-2.5h.01" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div class="payment-amount"><span class="payment-label">Баланс заказа</span><strong>${integer(available)} <span>₽</span></strong><span class="payment-confirmed${paidChoice.state==="DELETED"?" is-invalid":""}">${icon('check')}${paidChoice.state==="DELETED"?"Код удалён":available<=0?"Баланс использован":"Оплата применена"}</span></div><div class="payment-owner"><span class="payment-label">Получатель</span><strong>@${esc(paidChoice.recipient)}</strong>${Number(paidChoice.paid_rub)!==available?`<span class="payment-total">Всего оплачено ${integer(paidChoice.paid_rub)} ₽</span>`:""}</div>`
    : `<span class="voucher-gift" aria-hidden="true">${icon('gift')}</span><div class="voucher-copy"><strong>Уже оплатили? Введите код из сообщения.</strong><span>Примените оплату и выберите подарок в каталоге.</span></div>`;
  if($("#paid-summary").innerHTML!==summary)$("#paid-summary").innerHTML=summary;
  document.querySelectorAll("[data-ton-price]").forEach(el=>{el.textContent=rubPrice(el.dataset.tonPrice);});
  renderPaidDetail();
}
async function applyPaymentCode(code) {
  paidChoiceLoading=true;renderPaidDetail();
  try {
    paidChoice=realCredit(await api("/api/paid-choice/activate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code})}));
    paymentCodeOpen=false;
    $("#payment-code").value="";$("#payment-code-error").textContent="";
    announce("Оплата применена. Выберите подарок");
  } catch(error){$("#payment-code-error").textContent=error.message;}
  finally{paidChoiceLoading=false;renderPaidChoice();}
}
async function verifyPaidGift(confirmedRecipient=null) {
  if(!currentGift || !paidChoice)return;
  if(paidChoice.state==="AWAITING_TOPUP"){
    if(paidChoice.selected?.id===currentGift.id && paidChoice.payment_url && paidChoice.expires_at>Date.now()/1000)window.open(paidChoice.payment_url,"_blank","noopener");
    return;
  }
  if(paidChoice.state!=="AWAITING_SELECTION")return;
  if(paidChoice.state!=="AWAITING_TOPUP" && confirmedRecipient===null){
    recipientChoice={giftId:currentGift.id,sessionRevision:paymentSessionRevision};
    $("#recipient-username").textContent="@"+paidChoice.recipient;
    $("#recipient-input").value="@"+paidChoice.recipient;
    $("#recipient-edit").hidden=!!paidChoice.recipient;$("#recipient-error").textContent="";
    $("#recipient-dialog").showModal();if(!paidChoice.recipient)$("#recipient-input").focus();return;
  }
  const button=$("#check-gift"),selectedId=currentGift.id;
  button.disabled=true;
  try {
    button.textContent="Подтверждаем выбор…";
    paidChoice=realCredit(await api("/api/paid-choice/select/"+encodeURIComponent(selectedId),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({price_rub:giftSaleRub(currentGift),recipient:confirmedRecipient})}));
    if($("#recipient-dialog").open)$("#recipient-dialog").close();
    renderPaidChoice();
  } catch(error){$("#check-result").textContent=error.message;$("#check-result").classList.add("error");if($("#recipient-dialog").open)$("#recipient-error").textContent=error.message;button.disabled=false;}
}
$("#recipient-change").addEventListener("click",()=>{$("#recipient-edit").hidden=false;$("#recipient-input").focus();$("#recipient-input").select();});
$("#recipient-input").addEventListener("input",()=>{$("#recipient-username").textContent="@"+$("#recipient-input").value.trim().replace(/^@/,"");});
$("#recipient-close").addEventListener("click",()=>{if(!recipientBusy)$("#recipient-dialog").close();});
$("#recipient-dialog").addEventListener("cancel",event=>{if(recipientBusy)event.preventDefault();});
$("#recipient-dialog").addEventListener("close",()=>{recipientChoice=null;});
$("#recipient-form").addEventListener("submit",async event=>{
  event.preventDefault();if(recipientBusy)return;
  const recipient=$("#recipient-input").value.trim().replace(/^@/,"");
  if(!/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(recipient)){$("#recipient-error").textContent="Введите юзернейм: 5–32 символа, латинские буквы, цифры и подчёркивание.";return;}
  if(!recipientChoice || recipientChoice.giftId!==currentGift?.id || recipientChoice.sessionRevision!==paymentSessionRevision){$("#recipient-error").textContent="Заказ изменился. Откройте карточку подарка заново.";return;}
  recipientBusy=true;$("#recipient-error").textContent="";
  ["#recipient-yes","#recipient-change","#recipient-close","#recipient-input"].forEach(id=>$(id).disabled=true);
  try{await verifyPaidGift(recipient);}finally{recipientBusy=false;["#recipient-yes","#recipient-change","#recipient-close","#recipient-input"].forEach(id=>$(id).disabled=false);}
});
$("#payment-code-form").addEventListener("submit",event=>{event.preventDefault();applyPaymentCode($("#payment-code").value);});
$("#leave-payment").addEventListener("click",async()=>{
  const button=$("#leave-payment");button.disabled=true;
  try {
    await api("/api/paid-choice",{method:"DELETE"});
    paymentSessionRevision++;paidChoice=null;paymentCodeOpen=false;
    $("#payment-code-error").textContent="";$("#payment-code").value="";
    if($("#gift-dialog").open)$("#gift-dialog").close();
    renderPaidChoice();$("#payment-code").focus();
    announce("Вы вышли из заказа. Баланс сохранён.");
  } catch(error){$("#payment-code-error").textContent=error.message;}
  finally{button.disabled=false;}
});
$("#payment-code-cancel").addEventListener("click",()=>{paymentCodeOpen=false;$("#payment-code-error").textContent="";renderPaidChoice();$("#leave-payment").focus();});
async function initPaidChoice() {
  const code=new URLSearchParams(location.hash.slice(1)).get("code");
  if(code){history.replaceState(null,"",location.pathname+location.search+"#catalog");await applyPaymentCode(code);}
  else {try{paidChoice=realCredit(await api("/api/paid-choice"));}catch{}finally{paidChoiceLoading=false;renderPaidChoice();}}
}
initPaidChoice();
setInterval(async()=>{
  if(!paidChoice || document.hidden)return;
  const revision=paymentSessionRevision;
  try{const updated=realCredit(await api("/api/paid-choice"));if(revision!==paymentSessionRevision)return;paidChoice=updated;renderPaidChoice();}catch(error){
    if(revision!==paymentSessionRevision)return;
    if(error.message==="Код удалён владельцем") {paidChoice={...paidChoice,state:"DELETED"};renderPaidChoice();}
  }
},3000);
