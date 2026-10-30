"use client";
import { Loader2, X, AlertCircle, Check, ArrowUpRight, Layers } from "lucide-react";
import { Children, cloneElement, isValidElement, useId, type ReactNode } from "react";
export const money = (value: unknown, currency = "₽") => value === null || value === undefined ? "—" : `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ${currency}`;
export const date = (value: unknown) => !value ? "Ещё не было" : new Date(typeof value === "number" ? value * 1000 : String(value)).toLocaleString("ru-RU", { timeZone: "Europe/Volgograd", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
export function Spinner() { return <Loader2 className="spin" size={16} />; }
export function Button({ children, onClick, disabled, secondary, danger, type = "button" }: { children: ReactNode; onClick?: () => void; disabled?: boolean; secondary?: boolean; danger?: boolean; type?: "button" | "submit" }) {
  return <button type={type} disabled={disabled} className={`btn ${secondary ? "secondary" : ""} ${danger ? "danger" : ""}`} onClick={onClick}>{children}</button>;
}
export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) { return <span className={`badge ${tone}`}><i />{children}</span>; }
export function State({ value }: { value: string }) {
  const labels: Record<string, string> = { CONNECTED: "Подключён", NOT_CONFIGURED: "Не настроен", NOT_CHECKED: "Не проверен", CHECKING: "Проверка", ERROR: "Ошибка", APPROVED: "Активен", SOLD: "Продан", MANUAL: "Вручную", HISTORICAL: "История", DISCOVERED: "Новый", AWAITING_ENABLE: "Выдача выключена", COMPLETE: "Завершён", PREVIEW: "Расчёт", QUEUED: "В очереди", UNKNOWN: "Нужна сверка", FAILED: "Ошибка", BLOCKED: "Недоступно", READY: "Готово", RUNNING: "Выполняется", CANCELLED: "Отменён", IDLE: "Работает", BUSY: "Работает", STOPPED: "Остановлен", WAITING_DATA: "Нужен получатель", CHECK_PRICE: "Проверка цены", PURCHASED: "Куплен", WITHDRAWN: "Вывод запрошен", TRANSFERRED: "Передан", PURCHASING: "Закупка" };
  const tone = ["CONNECTED", "APPROVED", "COMPLETE", "READY", "IDLE", "BUSY"].includes(value) ? "green" : ["ERROR", "FAILED", "BLOCKED"].includes(value) ? "red" : ["UNKNOWN", "MANUAL", "AWAITING_ENABLE", "PREVIEW"].includes(value) ? "amber" : "neutral";
  return <Badge tone={tone}>{labels[value] || value}</Badge>;
}
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) { return <div className={`notice ${error ? "error" : ""}`}><AlertCircle size={17} /><div>{children}</div></div>; }
export function Panel({ title, subtitle, action, children }: { title?: string; subtitle?: string; action?: ReactNode; children: ReactNode }) {
  return <section className="panel">{title && <div className="panel-head"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>}{children}</section>;
}
export function Field({ label, children, help }: { label: string; children: ReactNode; help?: string }) {
  const helpId = useId();
  const controls = Children.map(children, child => isValidElement<Record<string, unknown>>(child) && ["input", "select", "textarea"].includes(String(child.type))
    ? cloneElement(child, { "aria-label": child.props["aria-label"] || label, ...(help ? { "aria-describedby": helpId } : {}) }) : child);
  return <label className="field"><span>{label}</span>{controls}{help && <small id={helpId}>{help}</small>}</label>;
}
export function Toggle({ label, checked, onChange, description, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; description?: string; disabled?: boolean }) {
  return <label className="toggle-row"><div><strong>{label}</strong>{description && <small>{description}</small>}</div><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} disabled={disabled} /><span className="switch" /></label>;
}
export function Empty({ text = "Пока нет данных", detail = "Данные появятся после синхронизации." }: { text?: string; detail?: string }) {
  return <div className="empty"><span className="empty-mark"><Layers size={21} aria-hidden="true" /></span><h3>{text}</h3><p>{detail}</p></div>;
}
export function Dialog({ title, children, onClose, footer, busy }: { title: string; children: ReactNode; onClose: () => void; footer?: ReactNode; busy?: boolean }) {
  return <div className="overlay" onClick={() => !busy && onClose()}><section role="dialog" aria-modal="true" aria-label={title} className="dialog" onClick={e => e.stopPropagation()}><header><h2>{title}</h2><button className="icon-button" aria-label="Закрыть" onClick={onClose} disabled={busy}><X size={19} /></button></header><div className="dialog-body">{children}</div>{footer && <footer>{footer}</footer>}</section></div>;
}
export function External({ href }: { href: string }) { return <a className="icon-button" href={href} target="_blank" rel="noreferrer" title="Открыть на Playerok"><ArrowUpRight size={18} /></a>; }
export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="chart-empty">История появится после следующих проверок цен</div>;
  const max = Math.max(...values), min = Math.min(...values), spread = max - min || 1;
  const points = values.map((v, i) => `${i / (values.length - 1) * 600},${130 - (v - min) / spread * 110}`).join(" ");
  return <svg viewBox="0 0 600 160" className="chart" role="img" aria-label="История изменения цены"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop stopColor="var(--accent)" stopOpacity=".2" /><stop offset="1" stopColor="var(--accent)" stopOpacity="0" /></linearGradient></defs>{[30, 70, 110, 150].map(y => <line key={y} x1="0" y1={y} x2="600" y2={y} stroke="var(--border)" />)}<polygon points={`0,160 ${points} 600,160`} fill="url(#fill)" /><polyline points={points} stroke="var(--accent)" strokeWidth="2.5" fill="none" /></svg>;
}
