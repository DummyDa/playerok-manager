"use client";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { BookOpen, KeyRound, RefreshCw } from "lucide-react";
import { api, waitTask } from "../lib/api";
import type { Actions } from "./Dashboard";
import { Badge, Button, Dialog, Field, Notice, Panel, Spinner, State, date } from "./ui";

const fetcher = (path: string) => api(path);
const pendingStates = ["QUEUED", "REQUESTING_CODE", "VERIFYING"];
const activeStates = [...pendingStates, "CODE_REQUIRED", "PASSWORD_REQUIRED"];

export default function ConnectionSetup({ act, busy }: Actions) {
  const config = useSWR("/connections/config", fetcher, { refreshInterval: 5000 });
  const connections = useSWR("/connections", fetcher, { refreshInterval: 5000 });
  const login = useSWR("/connections/telegram-user/login", fetcher, { refreshInterval: 1000 });
  const settings = useSWR("/settings", fetcher);
  const [draft, setDraft] = useState<any>(null);
  const [cookies, setCookies] = useState("");
  const [token, setToken] = useState("");
  const [hash, setHash] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [proxies, setProxies] = useState({ playerok_proxy: "", telegram_proxy: "", mtproto_proxy: "" });
  const [confirm, setConfirm] = useState<"playerok" | "telegram-user" | "bot" | null>(null);
  const c = config.data || {};
  const state = login.data?.state || "IDLE";
  const locked = busy || !settings.data?.dry_run;
  const signingIn = activeStates.includes(state);
  useEffect(() => {
    if (config.data && !draft) setDraft({ owner_username: c.owner_username || "", owner_chat_id: c.owner_chat_id || "", api_id: c.api_id || "", playerok_user_agent: c.playerok_user_agent || "", playerok_request_timeout: c.playerok_request_timeout || 20, public_origin: c.public_origin || "", mrkt_api_url: c.mrkt_api_url || "https://api.tgmrkt.io/api/v1" });
  }, [config.data, draft]);
  const edit = (key: string, value: any) => setDraft((d: any) => ({ ...d, [key]: value }));
  const connection = (id: string) => connections.data?.items?.find((r: any) => r.id === id);
  async function check(name: string) {
    await act(async () => { const result = await api("/connections/check", "POST", { name }); await waitTask(result.task_id); }, "Проверка подключения завершена");
  }
  async function saveTelegramKeys() {
    await api("/connections/config", "PATCH", { api_id: Number(draft.api_id), ...(hash.trim() ? { api_hash: hash.trim() } : {}) });
    setHash("");
  }
  async function submitLogin(action: "code" | "password") {
    const value = action === "code" ? code : password;
    if (action === "code") setCode(""); else setPassword("");
    await act(async () => { const result = await api(`/connections/telegram-user/${action}`, "POST", { flow_id: login.data.flow_id, value }); await login.mutate(result, false); }, "Данные переданы воркеру для проверки");
  }
  if (config.error) return <Notice error>Не удалось загрузить параметры подключений. Обновите страницу.</Notice>;
  if (!draft) return <Panel title="Подключения"><div className="loading"><Spinner />Загрузка настроек</div></Panel>;
  return <>
    <div className="connection-guide"><BookOpen size={18} /><div><strong>Подключите площадки без редактирования файлов</strong><span>Секреты сохраняются локально в зашифрованном виде. Пустое поле токена или API Hash оставляет сохранённое значение.</span></div><a className="text-link" href="/guide.html" target="_blank" rel="noreferrer">Пошаговая инструкция</a></div>
    {!settings.data?.dry_run && <Notice>Для смены аккаунтов, токенов и сетевых параметров сначала включите и сохраните Dry Run ниже.</Notice>}
    <div className="two-columns connection-setup">
      <Panel title="1. Playerok" subtitle="Cookies аккаунта продавца" action={<State value={connection("playerok")?.status || "NOT_CONFIGURED"} />}>
        <div className="form-pad">
          <p className="connection-help">Войдите на playerok.com в браузере. Откройте F12 → Network, обновите страницу, выберите запрос к playerok.com и скопируйте значение заголовка Cookie из Request Headers.</p>
          <Field label="Cookies Playerok" help={c.playerok_cookies_configured ? "Cookies уже сохранены. Для замены вставьте новую строку." : "Вставьте только значение Cookie, без названия заголовка."}><textarea className="secret" rows={3} value={cookies} onChange={e => setCookies(e.target.value)} spellCheck={false} autoComplete="off" /></Field>
          <div className="button-group"><Button disabled={locked || cookies.trim().length < 10} onClick={() => act(async () => { await api("/connections/playerok", "PUT", { cookies }); setCookies(""); }, "Cookies сохранены; проверка запущена")} >Сохранить cookies</Button><Button secondary disabled={busy || !c.playerok_cookies_configured} onClick={() => check("playerok")}><RefreshCw size={14} />Проверить</Button>{c.playerok_cookies_configured && <Button danger disabled={locked} onClick={() => setConfirm("playerok")}>Отключить</Button>}</div>
          <ConnectionDetail data={connection("playerok")} />
        </div>
      </Panel>
      <Panel title="2. Telegram-бот" subtitle="Уведомления и команды владельца" action={<State value={connection("telegram")?.status || "NOT_CONFIGURED"} />}>
        <div className="form-pad">
          <p className="connection-help">Создайте бота у <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">@BotFather</a> командой /newbot. Укажите токен и свой username либо числовой Telegram ID. После сохранения включите уведомления ниже и отправьте своему боту /start.</p>
          <Field label="Токен Telegram-бота" help={c.bot_configured ? "Токен сохранён. Пустое поле сохраняет текущий токен." : "Токен выдаёт BotFather."}><input type="password" autoComplete="new-password" value={token} onChange={e => setToken(e.target.value)} /></Field>
          <div className="form-grid"><Field label="Username владельца"><input placeholder="Без @" value={draft.owner_username} onChange={e => edit("owner_username", e.target.value.replace(/^@/, ""))} /></Field><Field label="Telegram ID владельца" help="Числовой ID надёжнее username. Достаточно заполнить одно из двух полей."><input type="number" min={1} value={draft.owner_chat_id} onChange={e => edit("owner_chat_id", e.target.value)} /></Field></div>
          <div className="button-group"><Button disabled={locked || (!draft.owner_username && !draft.owner_chat_id)} onClick={() => act(async () => { await api("/connections/config", "PATCH", { owner_username: draft.owner_username, ...(draft.owner_chat_id ? { owner_chat_id: Number(draft.owner_chat_id) } : {}), ...(token.trim() ? { bot_token: token.trim() } : {}) }); setToken(""); }, "Настройки бота сохранены")} >Сохранить бота</Button><Button secondary disabled={busy || !c.bot_configured} onClick={() => check("telegram")}>Проверить бота</Button>{c.bot_configured && <Button danger disabled={locked} onClick={() => setConfirm("bot")}>Удалить токен</Button>}</div>
          <ConnectionDetail data={connection("telegram")} />
        </div>
      </Panel>
    </div>
    <Panel title="3. Личный Telegram и MRKT" subtitle="Вход по номеру, коду и паролю 2FA. Business-бот не требуется." action={<Badge tone={c.user_session_configured ? "green" : "neutral"}>{c.user_session_configured ? "Telegram сохранён" : "Вход не выполнен"}</Badge>}>
      <div className="form-pad">
        <p className="connection-help">Получите API ID и API Hash на <a href="https://my.telegram.org/apps" target="_blank" rel="noreferrer">my.telegram.org/apps</a> → API development tools. Это реквизиты приложения Telegram, а не токен бота. Один личный аккаунт используется для MRKT и передачи NFT.</p>
        <div className="form-grid"><Field label="Telegram API ID"><input type="number" min={1} value={draft.api_id} disabled={locked || signingIn} onChange={e => edit("api_id", e.target.value)} /></Field><Field label="Telegram API Hash" help={c.api_hash_configured ? "Hash сохранён. Для сохранения старого оставьте поле пустым." : "32 символа из API development tools."}><input type="password" autoComplete="new-password" disabled={locked || signingIn} value={hash} onChange={e => setHash(e.target.value)} /></Field></div>
        {!signingIn && <>
          <Field label="Номер Telegram" help="Международный формат, например +79990000000. Код обычно приходит в приложение Telegram."><input type="tel" autoComplete="tel" value={phone} disabled={locked} onChange={e => setPhone(e.target.value)} /></Field>
          <div className="button-group"><Button secondary disabled={locked || signingIn || !draft.api_id || (!hash && !c.api_hash_configured)} onClick={() => act(saveTelegramKeys, "API ID и API Hash сохранены")}>Сохранить ключи</Button><Button disabled={locked || !phone.trim() || !draft.api_id || (!hash && !c.api_hash_configured)} onClick={() => act(async () => { await saveTelegramKeys(); const result = await api("/connections/telegram-user/start", "POST", { phone: phone.replace(/[\s()-]/g, "") }); await login.mutate(result, false); }, "Запрос кода поставлен в очередь")}><KeyRound size={15} />Получить код</Button></div>
        </>}
        {pendingStates.includes(state) && <div className="panel-note"><Spinner />{state === "QUEUED" ? "Воркер запрашивает код Telegram…" : "Проверяем данные входа…"}</div>}
        {state === "CODE_REQUIRED" && <div className="telegram-auth-step"><Field label="Код из Telegram"><input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} /></Field><Button disabled={locked || code.trim().length < 4} onClick={() => submitLogin("code")}>Подтвердить код</Button></div>}
        {state === "PASSWORD_REQUIRED" && <div className="telegram-auth-step"><Field label="Пароль двухэтапной проверки Telegram"><input type="password" autoComplete="off" value={password} onChange={e => setPassword(e.target.value)} /></Field><Button disabled={locked || !password} onClick={() => submitLogin("password")}>Подтвердить пароль</Button></div>}
        {login.data?.error && <Notice error>{login.data.error}</Notice>}
        {signingIn && <button className="text-link" disabled={busy} onClick={() => act(async () => { const result = await api("/connections/telegram-user/cancel", "POST", { flow_id: login.data.flow_id }); setCode(""); setPassword(""); await login.mutate(result, false); }, "Вход отменён")}>Отменить вход</button>}
        {c.user_session_configured && <div className="connected-account"><div><strong>{c.telegram_account?.username ? "@" + c.telegram_account.username : "Telegram-сессия сохранена"}</strong><small>MRKT авторизуется через этот аккаунт, пароль MRKT не требуется.</small></div><div className="button-group"><Button secondary disabled={busy} onClick={() => check("mrkt")}>Подключить / проверить MRKT</Button><Button danger disabled={locked || signingIn} onClick={() => setConfirm("telegram-user")}>Отключить Telegram</Button></div></div>}
        <ConnectionDetail data={connection("mrkt")} />
      </div>
    </Panel>
    <Panel title="Дополнительные параметры подключений" subtitle="По умолчанию используется прямое соединение. Прокси необязателен.">
      <div className="form-pad"><details><summary>Прокси, User-Agent и адрес MRKT API</summary>
        <Field label="User-Agent Playerok"><input value={draft.playerok_user_agent} onChange={e => edit("playerok_user_agent", e.target.value)} /></Field>
        <div className="form-grid"><Field label="Таймаут Playerok, секунд"><input type="number" min={5} max={120} value={draft.playerok_request_timeout} onChange={e => edit("playerok_request_timeout", Number(e.target.value))} /></Field><Field label="Адрес MRKT API"><input value={draft.mrkt_api_url} onChange={e => edit("mrkt_api_url", e.target.value)} /></Field></div>
        <Field label="Публичный HTTPS-адрес панели" help="Для доступа через свой HTTPS-прокси или туннель. Пустое значение разрешает только локальную панель. Не меняйте его из удалённой вкладки: можно потерять доступ."><input placeholder="https://panel.example.com" value={draft.public_origin} onChange={e => edit("public_origin", e.target.value)} /></Field>
        {([ ["playerok_proxy", "Прокси Playerok", c.playerok_proxy_configured], ["telegram_proxy", "Прокси Telegram Bot API", c.telegram_proxy_configured], ["mtproto_proxy", "MTProxy личного Telegram", c.mtproto_proxy_configured] ] as const).map(([key, label, saved]) => <div key={key}><Field label={label} help={key === "mtproto_proxy" ? "Ссылка tg://proxy?server=...&port=...&secret=..." : "http://user:password@host:port или socks5://host:port"}><input type="password" autoComplete="new-password" placeholder={saved ? "Сохранён; оставьте пустым, чтобы не менять" : "Не используется"} value={proxies[key]} onChange={e => setProxies({ ...proxies, [key]: e.target.value })} /></Field>{saved && <button className="text-link" disabled={locked} onClick={() => act(() => api("/connections/config", "PATCH", { [key]: "" }), "Прокси отключён")}>Отключить этот прокси</button>}</div>)}
        <Button disabled={locked} onClick={() => act(async () => { const filled = Object.fromEntries(Object.entries(proxies).filter(([,v]) => v.trim()).map(([k,v]) => [k,v.trim()])); await api("/connections/config", "PATCH", { ...filled, playerok_user_agent: draft.playerok_user_agent, playerok_request_timeout: Number(draft.playerok_request_timeout), mrkt_api_url: draft.mrkt_api_url, public_origin: draft.public_origin }); setProxies({ playerok_proxy: "", telegram_proxy: "", mtproto_proxy: "" }); }, "Сетевые настройки сохранены")}>Сохранить параметры сети</Button>
      </details></div>
    </Panel>
    {confirm && <Dialog title="Отключить подключение?" busy={busy} onClose={() => setConfirm(null)} footer={<><Button secondary disabled={busy} onClick={() => setConfirm(null)}>Отмена</Button><Button danger disabled={locked} onClick={() => act(async () => { if (confirm === "bot") await api("/connections/config", "PATCH", { bot_token: "" }); else await api("/connections/" + confirm, "DELETE"); setConfirm(null); }, "Подключение отключено")}>Отключить</Button></>}><p>История останется в панели. Торговые разрешения для отключённого подключения будут выключены. Личную сессию также можно отозвать в Telegram → Устройства.</p></Dialog>}
  </>;
}

function ConnectionDetail({ data }: { data: any }) {
  if (!data) return null;
  return <div className="connection-detail"><small>Последнее успешное обращение: {date(data.last_success)}</small>{data.username && <small>Бот: @{data.username}</small>}{data.last_error && <Notice error>{data.last_error}</Notice>}</div>;
}

export function AdminPassword({ act, busy }: Actions) {
  const [current, setCurrent] = useState(""), [next, setNext] = useState("");
  return <Panel title="Пароль панели" subtitle="После смены пароля другие сеансы панели завершатся"><div className="form-pad"><div className="form-grid"><Field label="Текущий пароль панели"><input type="password" autoComplete="current-password" value={current} onChange={e => setCurrent(e.target.value)} /></Field><Field label="Новый пароль панели" help="Не менее 10 символов"><input type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} /></Field></div><Button disabled={busy || !current || next.length < 10} onClick={() => act(async () => { await api("/auth/password", "POST", { current_password: current, new_password: next }); setCurrent(""); setNext(""); }, "Пароль панели изменён")}>Изменить пароль</Button></div></Panel>;
}
