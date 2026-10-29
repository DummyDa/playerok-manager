/* No debugger, CDP, window activation, or separate browser profile. */
const BASE = "http://127.0.0.1:8101/api/v1/browser-bridge/";
let busy = false;
async function request(path, body) {
  const { token } = await chrome.storage.local.get("token");
  const response = await fetch(BASE + path, {
    method: body === undefined ? "GET" : "POST", cache: "no-store",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + (token || "") },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Локальный сервер отклонил запрос");
  return data;
}
async function managedTab() {
  const { tabId, activeJob } = await chrome.storage.local.get(["tabId", "activeJob"]);
  let tab;
  try { if (tabId) tab = await chrome.tabs.get(tabId); } catch {}
  const valid = tab && /^https:\/\/web\.telegram\.org\/a\//.test(tab.url || "");
  if (valid && activeJob) return tab;
  if (valid && (await attach(tab.id))?.ready) return tab;
  // Prefer an already booted WebA session over another empty background tab.
  const candidates = await chrome.tabs.query?.({ url: "https://web.telegram.org/a/*" }) || [];
  for (const candidate of candidates) {
    if (candidate.id === tab?.id) continue;
    if ((await attach(candidate.id))?.ready) {
      await chrome.tabs.update(candidate.id, { autoDiscardable: false });
      await chrome.storage.local.set({ tabId: candidate.id });
      return candidate;
    }
  }
  if (valid) return tab;
  const windows = await chrome.windows.getAll({ windowTypes: ["normal"] });
  // Creating a tab without an existing window could bring Chrome to the front.
  if (!windows.length) throw new Error("Откройте обычное окно Chrome");
  tab = await chrome.tabs.create({ windowId: windows[0].id, url: "https://web.telegram.org/a/", active: false });
  await chrome.tabs.update(tab.id, { autoDiscardable: false });
  await chrome.storage.local.set({ tabId: tab.id });
  return tab;
}
async function attach(tabId) {
  try {
    const probe = await chrome.tabs.sendMessage(tabId, { type: "probe" });
    if (typeof probe?.ready === "boolean") return probe;
  } catch {}
  if (!chrome.scripting?.executeScript) return null;
  try {
    // Static local code in the isolated extension world; no page credentials or
    // Telegram globals are exported. This does not reload or focus the tab.
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return await chrome.tabs.sendMessage(tabId, { type: "probe" });
  } catch { return null; }
}
async function probeTab(tab) {
  try {
    const result = await chrome.tabs.sendMessage(tab.id, { type: "probe" });
    if (typeof result?.ready === "boolean" && !result.needsReload) return result;
  } catch {}
  const saved = await chrome.storage.local.get(["activeJob", "lastProbeReload"]);
  if (saved.activeJob) {
    // Never destroy a confirmation/payment dialog after losing its connection.
    const state = await request("job/" + encodeURIComponent(saved.activeJob.id));
    if (!["COMPLETE", "FAILED", "CANCELLED", "UNKNOWN"].includes(state.state)) {
      return { ready: false, error: "Связь с текущей передачей потеряна. Ожидаем сверку результата, вкладка не перезагружается" };
    }
    await chrome.storage.local.remove("activeJob");
  }
  const last = saved.lastProbeReload;
  if (last?.tabId === tab.id && Date.now() - last.at < 60000) {
    return { ready: false, error: "Восстанавливаем связь с Telegram Web. Ожидаем загрузку вкладки" };
  }
  // Reloading an extension invalidates its content script in existing tabs.
  // A background reload restores the script without activating the window.
  await chrome.storage.local.set({ lastProbeReload: { tabId: tab.id, at: Date.now() } });
  await chrome.tabs.reload(tab.id);
  for (let i = 0; i < 40; i++) {
    await new Promise(resolve => setTimeout(resolve, 500));
    try {
      const result = await chrome.tabs.sendMessage(tab.id, { type: "probe" });
      if (typeof result?.ready === "boolean") return result;
    } catch {}
  }
  return { ready: false, error: "Telegram Web пока не отвечает. Расширение повторит подключение автоматически" };
}
async function cycle() {
  if (busy) return;
  const { token } = await chrome.storage.local.get("token");
  if (!token) return;
  busy = true;
  try {
    const tab = await managedTab();
    const probe = await probeTab(tab);
    const error = ((probe?.error || "") + (probe.diagnostic ? " | " + JSON.stringify(probe.diagnostic) : "")).slice(0, 300);
    const { job } = await request("poll", { ready: !!probe?.ready, error, version: chrome.runtime.getManifest?.().version || "", app_visible: probe.app_visible ?? null, login_visible: probe.login_visible ?? null, page_state: probe.page_state || "unknown" });
    await chrome.storage.local.set({ status: probe?.ready ? "Подключено. Ожидаем заказ" : probe?.error });
    if (!job) return;
    // Only the attached service tab is navigated; other tabs are untouched.
    await chrome.storage.local.set({ activeJob: { id: job.id, lease: job.lease } });
    // A public NFT deep link opens a public card without the saved-gift handle
    // required for transfers. The content adapter opens the owned-gifts UI.
    let sent = false;
    for (let i = 0; i < 40; i++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      try {
        const result = await chrome.tabs.sendMessage(tab.id, { type: "prepare", job });
        if (result?.started) { sent = true; break; }
      } catch {}
    }
    if (!sent) await request("event", { job_id: job.id, lease: job.lease, event: "error", error: "Не удалось подготовить Telegram Web" });
  } catch (error) {
    await chrome.storage.local.set({ status: String(error.message).slice(0, 300) });
  } finally { busy = false; }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const fromOptions = sender.id === chrome.runtime.id && sender.url?.split(/[?#]/)[0] === chrome.runtime.getURL("options.html");
  if (sender.tab && !fromOptions) {
    (async () => {
      const { tabId, activeJob } = await chrome.storage.local.get(["tabId", "activeJob"]);
      if (sender.tab.id !== tabId || !sender.url?.startsWith("https://web.telegram.org/a/") || !activeJob || message.job_id !== activeJob.id || message.lease !== activeJob.lease) throw new Error("Нет активного задания");
      if (message.type === "job-status") return request("job/" + encodeURIComponent(activeJob.id));
      if (message.type !== "event") throw new Error("Неизвестное сообщение");
      const { type, ...body } = message;
      const result = await request("event", body);
      if (body.event === "done" || body.event === "error") await chrome.storage.local.remove("activeJob");
      return result;
    })().then(respond).catch(() => respond({ error: "Сервер не разрешил действие. Проверьте заказ в панели" }));
    return true;
  }
  if (message.type === "pair" && fromOptions) {
    (async () => {
      const response = await fetch(BASE + "pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: message.code }), signal: AbortSignal.timeout(10000) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось подключиться");
      await chrome.storage.local.set({ token: data.token });
      await cycle(); return { ok: true };
    })().then(respond).catch(error => respond({ error: error.message }));
    return true;
  }
});
async function schedule() { await chrome.alarms.create("delivery", { periodInMinutes: 0.5 }); await cycle(); }
chrome.alarms.onAlarm.addListener(() => cycle());
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

