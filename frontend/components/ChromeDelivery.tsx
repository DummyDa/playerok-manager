"use client";
import { useState } from "react";
import useSWR from "swr";
import { api } from "../lib/api";
import { Button, Field, Notice, Panel, date } from "./ui";
import type { Actions } from "./Dashboard";

export default function ChromeDelivery({ act, busy, onTransportSaved }: Actions & { onTransportSaved?: () => void }) {
  const { data, mutate } = useSWR<any>("/browser-delivery", api, { refreshInterval: 5000 });
  const settings = useSWR<any>("/settings", api);
  const [code, setCode] = useState("");
  const transport = settings.data?.delivery_transport || "mtproto";
  return <Panel title="Способ автовыдачи подарков" subtitle="Передача через сохранённую Telegram-сессию или Telegram Web A">
    <div className="form-pad">
      <Field label="Способ передачи NFT" help="Сохраняется сразу после выбора."><select value={transport} disabled={busy || !settings.data} onChange={e => act(async () => {
        const saved = await api("/settings", "PATCH", { delivery_transport: e.target.value });
        await settings.mutate(saved, { revalidate: false });
        onTransportSaved?.();
      }, "Способ передачи сохранён. Подтвердите тест выдачи перед включением.")}><option value="mtproto">Telegram-сессия — Telethon</option><option value="kurigram">Telegram-сессия — Kurigram</option><option value="chrome_extension">Telegram Web A + расширение Chrome</option></select></Field>
      {transport === "kurigram" && <p className="muted">Kurigram использует аккаунт из настроек подключения Telegram. Повторный вход не нужен. Перед включением автовыдачи проверьте передачу на тестовом заказе.</p>}
      <div hidden={transport !== "chrome_extension"}>
      <p>{data?.paired ? data.ready ? "Расширение готово к выдаче" : data.online ? "Chrome подключён. Требуется вход в Telegram Web" : "Расширение подключено, но Chrome сейчас недоступен" : "Расширение ещё не подключено"}</p>
      {data?.error && <Notice>{data.error}</Notice>}
      <p className="muted">Последний сигнал: {date(data?.heartbeat)}</p>
      <ol><li><a className="text-link" href="/api/v1/browser-delivery/extension.zip" download>Скачайте расширение</a> и распакуйте архив в постоянную папку.</li><li>В Chrome откройте chrome://extensions, включите режим разработчика и нажмите «Загрузить распакованное расширение».</li><li>Получите код ниже, откройте настройки расширения и вставьте код. Войдите в Telegram Web A в созданной вкладке.</li><li>Выберите выдачу через Chrome и проверьте передачу на тестовом заказе. После этого подтвердите проверку выдачи в разрешениях автоматизации.</li></ol>
      <div className="button-group"><Button disabled={busy} onClick={() => act(async () => { const result = await api("/browser-delivery/pairing", "POST"); setCode(result.code); }, "Код действует пять минут")}>Получить код подключения</Button>{data?.paired && <Button secondary disabled={busy} onClick={() => act(async () => { await api("/browser-delivery/revoke", "POST"); await mutate(); setCode(""); })}>Отключить расширение</Button>}</div>
      {code && <Field label="Одноразовый код для расширения"><input readOnly value={code} onFocus={e => e.target.select()} /></Field>}
      <Notice>Chrome должен оставаться запущенным. Подтверждение Playerok выполняется только после проверки владельца конкретного NFT. При обновлении расширения замените его файлы и нажмите «Обновить» в chrome://extensions.</Notice>
      {!!data?.jobs?.length && <div className="table-scroll"><table><thead><tr><th>NFT</th><th>Состояние</th><th>Ошибка</th></tr></thead><tbody>{data.jobs.map((job: any) => <tr key={job.id}><td>{job.slug}</td><td>{({ QUEUED: "Ожидает Chrome", CLAIMED: "Подготовка", READY: "Проверка", AUTHORIZED: "Разрешена передача", SUBMITTED: "Передача запрошена", VERIFYING: "Проверка владельца", COMPLETE: "Передан", UNKNOWN: "Нужна сверка", CANCELLED: "Остановлен", FAILED: "Ошибка" } as Record<string,string>)[job.state] || job.state}</td><td>{job.error || "—"}</td></tr>)}</tbody></table></div>}
      </div>
    </div>
  </Panel>;
}
