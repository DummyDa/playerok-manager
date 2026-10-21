"use client";
import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Boxes, ReceiptText, Gift, SlidersHorizontal, Percent, ScanLine, ArrowUp, ChartNoAxesCombined, History, Settings2, Sparkles, ShieldCheck, OctagonPause, LogOut, ArrowRight, RefreshCw, Menu, ChevronRight, Bell, Play } from "lucide-react";
import { api, setCsrf, typedClient } from "../lib/api";
import { Badge, Button, Field, Notice, Spinner, money } from "./ui";
import { Overview, Lots, Gifts, Pricing, Fees, Competitors, Promotions, Orders, Finance, Events, Settings } from "./Sections";
import { UpdateBanner } from "./Updates";

const nav = [
  ["", "Обзор", LayoutDashboard], ["lots", "Лоты", Boxes], ["orders", "Заказы", ReceiptText],
  ["gifts", "NFT Gifts", Gift], ["pricing", "Автоцены", SlidersHorizontal], ["fees", "Комиссии", Percent],
  ["competitors", "Конкуренты", ScanLine], ["promotions", "Поднятия", ArrowUp], ["finance", "Финансы", ChartNoAxesCombined],
  ["history", "История", History], ["settings", "Настройки", Settings2],
] as const;
const descriptions: Record<string, string> = {
  "": "Продажи, подарки и автоматизация — под контролем.", lots: "Объявления Playerok и точные привязки к NFT.", orders: "Каждый заказ — от оплаты до подтверждённой выдачи.",
  gifts: "Коллекции, модели и актуальные предложения MRKT.", pricing: "Безопасная цена с учётом закупки, комиссий и прибыли.", fees: "Прозрачный расчёт каждого удержания.",
  competitors: "Следите за предложениями той же коллекции и модели.", promotions: "Премиум для активных объявлений — вручную и по расписанию.",
  finance: "Фактические операции и прогнозы отдельно.", history: "Изменения, проверки и результаты действий системы.", settings: "Подключения, лимиты и разрешения на автоматизацию.",
};
export type Actions = { act: (fn: () => Promise<any>, text?: string) => Promise<any>; busy: boolean };

export default function Dashboard() {
  const auth = useSWR("/auth/status", async () => {
    const { data, error } = await typedClient.GET("/api/v1/auth/status");
    if (error) throw new Error("API недоступен");
    return data as { initialized: boolean; authenticated: boolean; csrf: string | null };
  }, { revalidateOnFocus: false });
  useEffect(() => { if (auth.data?.csrf) setCsrf(auth.data.csrf); }, [auth.data?.csrf]);
  if (auth.isLoading) return <div className="app-loading"><Spinner /><span>Подключаем панель</span></div>;
  if (auth.error) return <div className="app-loading"><Notice error>Не удалось связаться с API. Проверьте запуск приложения.</Notice><Button onClick={() => auth.mutate()}>Повторить</Button></div>;
  if (!auth.data?.authenticated) return <Login initialized={!!auth.data?.initialized} onLogin={() => auth.mutate()} />;
  return <Workspace />;
}

function Login({ initialized, onLogin }: { initialized: boolean; onLogin: () => void }) {
  const [password, setPassword] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  return <main className="login-page"><div className="login-art"><div className="brand"><span className="brand-logo"><Gift size={23} /></span><div>playerok<span>GIFT MANAGER</span></div></div><div className="login-hero"><div className="eyebrow">ВАШ МАГАЗИН. ВАШИ ПРАВИЛА.</div><h1>Меньше рутины.<br /><em>Больше контроля.</em></h1><p>Лоты, цены и Telegram-подарки<br />в одном рабочем пространстве.</p><div className="orb"><Gift size={94} strokeWidth={1} /></div><div className="login-tags"><Badge tone="green">Локальное хранение</Badge><Badge>Автоматизация под контролем</Badge></div></div><small className="login-bottom">PLAYEROK MANAGER 2 · WINDOWS EDITION</small></div><div className="login-form"><span className="square-icon"><ShieldCheck size={26} /></span><h2>{initialized ? "С возвращением" : "Ваше новое рабочее место"}</h2><p>{initialized ? "Войдите в локальную панель управления." : "Создайте пароль администратора. Все торговые действия пока выключены."}</p><form onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { const r = await api(initialized ? "/auth/login" : "/auth/setup", "POST", { password }); setCsrf(r.csrf); onLogin(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}><Field label="Пароль" help="Минимум 10 символов"><input type="password" minLength={10} autoComplete={initialized ? "current-password" : "new-password"} required value={password} onChange={e => setPassword(e.target.value)} /></Field>{error && <Notice error>{error}</Notice>}<Button type="submit" disabled={busy}>{busy ? <Spinner /> : null}{initialized ? "Войти в панель" : "Создать пароль и войти"}<ArrowRight size={17} /></Button></form><div className="local-note"><i />Доступ только с этого компьютера</div></div></main>;
}

function Workspace() {
  const path = usePathname().split("/")[1] || "";
  const selected = nav.find(n => n[0] === path) || nav[0];
  const [message, setMessage] = useState(""); const [error, setError] = useState(false); const [busy, setBusy] = useState(false); const [menu, setMenu] = useState(false);
  const [sidebarExpanded, setSidebarExpanded] = useState(false);
  const [sidebarAnimated, setSidebarAnimated] = useState(false);
  useEffect(() => { try { setSidebarExpanded(localStorage.getItem("playerok.sidebar-expanded") === "true"); } catch {} }, []);
  function toggleSidebar() {
    setSidebarAnimated(true);
    const expanded = !sidebarExpanded;
    setSidebarExpanded(expanded);
    try { localStorage.setItem("playerok.sidebar-expanded", String(expanded)); } catch {}
  }
  const settings = useSWR<any>("/settings", (path: string) => api(path), { refreshInterval: 5000 });
  const connections = useSWR<any>("/connections", (path: string) => api(path), { refreshInterval: 5000 });
  const mrkt = connections.data?.items?.find((c: any) => c.id === "mrkt");
  const worker = connections.data?.worker;
  const workerAlive = worker && Date.now() / 1000 - worker.heartbeat < 180;
  async function act(fn: () => Promise<any>, text = "Сохранено") {
    setBusy(true); setMessage(""); setError(false);
    try { const result = await fn(); setMessage(text); await mutate(key => typeof key === "string" && key.startsWith("/")); return result; }
    catch (e) { setError(true); setMessage((e as Error).message); return null; }
    finally { setBusy(false); }
  }
  const shared = { act, busy };
  const sections: Record<string, React.ReactNode> = {
    "": <Overview {...shared} />, lots: <Lots {...shared} />, gifts: <Gifts {...shared} />, fees: <Fees {...shared} />,
    pricing: <Pricing {...shared} />, competitors: <Competitors {...shared} />, promotions: <Promotions {...shared} />,
    orders: <Orders {...shared} />, finance: <Finance />, history: <Events />, settings: <Settings {...shared} />,
  };
  return <div className={`workspace ${sidebarExpanded ? "sidebar-expanded" : ""} ${sidebarAnimated ? "sidebar-motion" : ""}`}><a href="#main-content" className="skip-link">К содержимому</a>
    {menu && <button className="menu-backdrop" aria-label="Закрыть меню" onClick={() => setMenu(false)} />}
    <aside className={`sidebar ${menu ? "open" : ""}`}><nav id="sidebar-navigation" aria-label="Все разделы"><button type="button" className="sidebar-toggle" onClick={toggleSidebar} aria-expanded={sidebarExpanded} aria-controls="sidebar-navigation" aria-label={sidebarExpanded ? "Свернуть меню" : "Развернуть меню"} title={sidebarExpanded ? "Свернуть меню" : "Развернуть меню"}><ChevronRight size={19} strokeWidth={1.8} aria-hidden="true" /></button>{nav.map(([to, label, Icon]) => <Link prefetch={false} key={to} href={"/" + to} title={label} data-label={label} aria-label={label} aria-current={path === to ? "page" : undefined} onClick={() => setMenu(false)} className={path === to ? "selected" : ""}><Icon size={19} strokeWidth={1.65} aria-hidden="true" /><span>{label}</span></Link>)}</nav></aside>
    <div className="main-wrap">
      <header className="topbar">
        <button className="icon-button mobile-menu" onClick={() => setMenu(!menu)} aria-label="Меню" aria-expanded={menu}><Menu size={22} /></button>
        <Link prefetch={false} href="/" className="brand" aria-label="Playerok Manager — главная"><span className="brand-logo"><Gift size={23} /></span><div>playerok<span>GIFT MANAGER</span></div></Link>
        <nav className="top-nav" aria-label="Основные разделы">{nav.filter(n => ["", "lots", "orders", "gifts", "finance"].includes(n[0])).map(([to, label]) => <Link prefetch={false} key={to} href={"/"+to} className={path === to ? "selected" : ""} aria-current={path === to ? "page" : undefined}>{label}</Link>)}</nav>
        <div className="topbar-right"><Link prefetch={false} href="/history" className="icon-button" aria-label="История уведомлений" title="История уведомлений"><Bell size={18} /></Link><Link prefetch={false} href="/settings" className="account-chip"><span className="avatar">{(settings.data?.playerok_username || "P").slice(0, 1)}</span><div><strong>{settings.data?.playerok_username || "Мой магазин"}</strong><small><i className={workerAlive ? "alive" : ""} />{workerAlive ? "Система работает" : "Нет связи с воркером"}</small></div></Link><button className="icon-button" title="Выйти" aria-label="Выйти" onClick={() => act(async () => { await api("/auth/logout", "POST"); location.reload(); })}><LogOut size={18} /></button></div>
      </header>
      <main id="main-content" tabIndex={-1} className="content"><div className="page-heading"><div><div className="eyebrow">ВАШ МАГАЗИН ПОД КОНТРОЛЕМ</div><h1>{selected[1]}</h1><p>{descriptions[path]}</p></div><div className="heading-actions"><button className={settings.data?.emergency_stop ? "btn resume" : "btn stop"} disabled={busy || !settings.data} onClick={() => settings.data?.emergency_stop ? act(() => api("/settings", "PATCH", { emergency_stop: false }), "Автоматизация возобновлена") : act(() => api("/automation/stop", "POST"), "Новые торговые действия остановлены")}>{settings.data?.emergency_stop ? <Play size={16} /> : <OctagonPause size={16} />}{settings.data?.emergency_stop ? "Возобновить" : "Стоп"}</button></div></div>
      <UpdateBanner />{settings.error && <Notice error>{settings.error.message}</Notice>}{message && <div className="toast" role="status"><Notice error={error}>{message}</Notice><button aria-label="Скрыть уведомление" onClick={() => setMessage("")}>×</button></div>}{sections[path] || <Notice>Раздел не найден.</Notice>}</main>
      <footer className="app-footer"><span>PLAYEROK GIFT MANAGER <b>2.0</b></span><span>Данные на вашем компьютере · UTC+3</span></footer>
    </div>
  </div>;
}
