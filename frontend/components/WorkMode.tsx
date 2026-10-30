"use client";

import { useId } from "react";
import useSWR from "swr";
import { AlertTriangle, TrendingUp, Crown, Hexagon, Send, ClipboardCheck, Bot } from "lucide-react";
import { api } from "../lib/api";

const modes = [
  { key: "emergency_stop", title: "Аварийная остановка", description: "Останавливает новые торговые действия. Уже отправленные запросы требуют сверки.", icon: AlertTriangle },
  { key: "repricing_enabled", title: "Автоматическое изменение цен", description: "Обновляет цены активных лотов по вашим правилам и целевой прибыли.", icon: TrendingUp },
  { key: "promotions_enabled", title: "Платные поднятия", description: "Разрешает платные поднятия для увеличения видимости лотов.", icon: Crown },
  { key: "purchases_enabled", title: "Покупка NFT на MRKT", description: "Разрешает закупку подарков для оплаченных заказов с проверкой прибыли и лимитов.", icon: Hexagon },
  { key: "transfers_enabled", title: "Передача NFT покупателям", description: "Разрешает передачу купленных NFT указанным получателям.", icon: Send },
  { key: "delivery_tested", title: "Выдача отдельно протестирована мной", description: "Подтверждение владельца после сквозного теста. Одна полученная форма оплаты не подтверждает успешную передачу.", icon: ClipboardCheck },
  { key: "notifications_enabled", title: "Telegram-уведомления и команды", description: "Сначала остановите старый проект. Два экземпляра одного бота конфликтуют.", icon: Bot },
];

export default function WorkMode({ values, saved, update, busy }: {
  values: Record<string, any>; saved: Record<string, any>;
  update: (key: string, value: unknown) => void; busy: boolean;
}) {
  const id = useId();
  const connections = useSWR("/connections", path => api(path), { refreshInterval: 5000 });
  const pending = modes.some(mode => !!values[mode.key] !== !!saved[mode.key]);
  const worker = connections.data?.worker;
  const workerReady = worker && ["RUNNING", "IDLE", "BUSY"].includes(worker.status) && Date.now() / 1000 - worker.heartbeat < 120;
  const required = saved.notifications_enabled ? ["playerok", "mrkt", "telegram"] : ["playerok", "mrkt"];
  const connected = required.every(name => connections.data?.items?.some((item: any) => item.id === name && item.status === "CONNECTED"));
  const tone = pending ? "pending" : saved.emergency_stop ? "stopped" : !connections.error && connected && workerReady ? "ready" : "pending";
  const title = pending ? "Есть изменения" : saved.emergency_stop ? "Автоматизация остановлена" : connections.error ? "Статус недоступен" : tone === "ready" ? "Подключения в порядке" : "Требуется проверка";
  const detail = pending ? "Сохраните настройки" : saved.emergency_stop ? "Новые действия на паузе" : tone === "ready" ? "Фоновый обработчик работает" : "Проверьте подключения и обработчик";

  return <section className="work-mode" aria-labelledby={`${id}-title`}>
    <header className="work-mode-head">
      <div className="work-mode-heading"><h2 id={`${id}-title`}>Режим работы</h2><p>Настройте автоматизацию проекта. Разрешения проверяются перед каждым внешним изменением.</p></div>
      <div className={`work-mode-status ${tone}`} role="status"><span className="work-mode-status-dot" /><div><strong>{title}</strong><small>{detail}</small></div></div>
    </header>
    <div className="work-mode-list">{modes.map(({ key, title, description, icon: Icon }) => <label key={key} className={`work-mode-row ${values[key] ? "enabled" : ""} ${key === "emergency_stop" ? "emergency" : ""} ${busy ? "disabled" : ""}`}>
      <span className="work-mode-icon" aria-hidden="true"><Icon size={29} strokeWidth={1.8} /></span>
      <span className="work-mode-copy"><strong>{title}</strong><small id={`${id}-${key}-description`}>{description}</small></span>
      <span className="work-mode-control"><input type="checkbox" aria-label={title} aria-describedby={`${id}-${key}-description`} checked={!!values[key]} disabled={busy} onChange={event => update(key, event.target.checked)} /><span className="work-mode-switch" aria-hidden="true" /></span>
    </label>)}</div>
    <p className="work-mode-footnote">Изменения применятся после нажатия «Сохранить настройки».</p>
  </section>;
}
