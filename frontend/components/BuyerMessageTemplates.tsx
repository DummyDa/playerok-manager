"use client";
import { Field, Panel, Toggle } from "./ui";

export default function BuyerMessageTemplates({ values, update, busy }: {
  values: Record<string, any>;
  update: (key: string, value: unknown) => void;
  busy: boolean;
}) {
  return <Panel title="Ответы покупателю" subtitle="Автоматические сообщения в чат сделки Playerok">
    <div className="form-pad">
      <p className="muted">Настройте тексты и нажмите «Сохранить настройки». Сообщения отправляются для новых событий после включения; старым завершённым заказам ответы не отправляются. Шаблоны работают и при ручной выдаче. Оплата рекламных лотов и доплат настраивается во вкладке «Каталог».</p>
      <div className="form-grid">
        <div>
          <Toggle label="Ответ после оплаты" checked={!!values.buyer_paid_enabled} disabled={busy}
            onChange={value => update("buyer_paid_enabled", value)} />
          <Field label="Шаблон после оплаты" help="Пустой текст отключает отправку этого ответа.">
            <textarea rows={5} maxLength={4000} disabled={busy} value={values.buyer_paid_template || ""}
              onChange={event => update("buyer_paid_template", event.target.value)} />
          </Field>
        </div>
        <div>
          <Toggle label="Ответ после передачи NFT" checked={!!values.buyer_delivered_enabled} disabled={busy}
            onChange={value => update("buyer_delivered_enabled", value)} />
          <Field label="Шаблон после передачи NFT" help="Используется также после выдачи из каталога. Для каталога сообщение обязательно: пустой шаблон использует стандартный текст.">
            <textarea rows={5} maxLength={4000} disabled={busy} value={values.buyer_delivered_template || ""}
              onChange={event => update("buyer_delivered_template", event.target.value)} />
          </Field>
        </div>
      </div>
      <p className="muted">Подстановки: <code>{"{order_id}"}</code> — номер заказа; <code>{"{recipient}"}</code> — Telegram получателя; <code>{"{title}"}</code> — название лота; <code>{"{price}"}</code> — цена в рублях; <code>{"{gift}"}</code> — NFT; <code>{"{gift_link}"}</code> — ссылка на подарок; <code>{"{delivery_notice}"}</code> — пояснение о ручной выдаче, если она включена.</p>
    </div>
  </Panel>;
}
