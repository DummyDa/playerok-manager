"use client";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { ArrowUpCircle, ExternalLink, RefreshCw } from "lucide-react";
import { api, waitTask } from "../lib/api";
import { Button, Dialog, Notice, Panel, Spinner, date } from "./ui";

const active = ["QUEUED", "FETCHING", "STOPPING", "BACKUP", "INSTALLING", "RESTARTING", "ROLLING_BACK"];
// A server restart is expected during installation. Keep reconnecting even after
// a failed poll and when the tab is in the background.
const useUpdates = () => useSWR<any>("/updates", api, {
  refreshInterval: 5000, refreshWhenHidden: true, refreshWhenOffline: true,
  onErrorRetry: (_error, _key, _config, revalidate, { retryCount }) => {
    setTimeout(() => revalidate({ retryCount }), 3000);
  },
});
const pendingKey = "playerok.update-pending";
function rememberUpdate(target: string) {
  try { sessionStorage.setItem(pendingKey, JSON.stringify({ target, started: Date.now() })); } catch {}
}

export function UpdateBanner() {
  const update = useUpdates();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [openedVersion, setOpenedVersion] = useState("");
  const pendingTarget = useRef("");
  const reloading = useRef(false);
  const data = update.data;
  const completion = data?.progress?.state === "COMPLETE" ? `${data.progress.installed_commit || data.progress.target}:${data.progress.updated_at}` : "";
  useEffect(() => {
    if (!completion) return;
    try { setOpenedVersion(localStorage.getItem("playerok.update-opened") || sessionStorage.getItem("playerok.update-opened") || ""); } catch {}
  }, [completion]);
  useEffect(() => {
    if (!data || reloading.current) return;
    const progress = data.progress;
    if (active.includes(progress?.state) && progress.target) {
      pendingTarget.current = progress.target;
      rememberUpdate(progress.target);
      return;
    }
    if (!pendingTarget.current) {
      try {
        const saved = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
        if (saved && Date.now() - saved.started < 3600000) pendingTarget.current = saved.target;
      } catch {}
    }
    if (progress?.state === "FAILED") {
      pendingTarget.current = "";
      try { sessionStorage.removeItem(pendingKey); } catch {}
      return;
    }
    // COMPLETE is written only after the launcher has started the new frontend.
    // Also verify the API is serving the intended source version before reload.
    if (!completion || !pendingTarget.current || data.current_commit !== pendingTarget.current ||
        (progress.installed_commit || progress.target) !== pendingTarget.current) return;
    reloading.current = true;
    try {
      try { localStorage.setItem("playerok.update-opened", completion); }
      catch { try { sessionStorage.setItem("playerok.update-opened", completion); } catch {} }
      try { sessionStorage.removeItem(pendingKey); } catch {}
      setOpenedVersion(completion);
      location.reload();
    } catch {
      reloading.current = false;
      setError("Новая версия запущена. Нажмите «Открыть новую версию», чтобы обновить страницу.");
    }
  }, [data, completion]);
  if (!data) return null;
  const running = active.includes(data.progress?.state);
  const recentResult = ["COMPLETE", "FAILED"].includes(data.progress?.state) && (!completion || openedVersion !== completion) && Date.now() / 1000 - data.progress.updated_at < 1800;
  if ((!data.available || data.dismissed) && !running && !recentResult) return null;
  async function install() {
    setBusy(true); setError("");
    try {
      await api("/updates/install", "POST", { commit: data.latest_commit });
      pendingTarget.current = data.latest_commit; rememberUpdate(data.latest_commit);
      setOpen(false);
      // A missing response here means services may already be stopping, not a
      // failed installation. The reconnecting poll will recover automatically.
      await update.mutate().catch(() => undefined);
    }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function openNewVersion() {
    try {
      try { localStorage.setItem("playerok.update-opened", completion); }
      catch { sessionStorage.setItem("playerok.update-opened", completion); }
      setOpenedVersion(completion);
      location.reload();
    } catch { setError("Не удалось перезагрузить интерфейс. Обновите страницу через Ctrl+F5."); }
  }
  return <>
    <section className="update-banner" aria-label="Обновление приложения" role="status">
      <span className="update-icon">{running ? <Spinner /> : <ArrowUpCircle size={23} />}</span>
      <div className="update-copy"><strong>{running ? "Устанавливаем обновление" : recentResult ? data.progress.state === "COMPLETE" ? "Приложение обновлено" : "Обновление не установлено" : "Доступно обновление Playerok Manager"}</strong><span>{running && update.error ? "Приложение временно недоступно: ожидаем завершения установки и запуска служб." : running || recentResult ? data.progress.message : (data.message || "В GitHub появилась новая версия").split("\n")[0]}</span>{running && <small>Новая версия откроется автоматически. Перезапускать приложение вручную не нужно.</small>}</div>
      {!running && !recentResult && <div className="button-group"><Button secondary onClick={async () => { await api("/updates/dismiss", "POST", { commit: data.latest_commit }); await update.mutate(); }}>Позже</Button><Button onClick={() => { setError(""); setOpen(true); }}>Посмотреть обновление</Button></div>}
      {recentResult && data.progress.state === "COMPLETE" && <Button onClick={openNewVersion}>Открыть новую версию</Button>}
      {error && !open && <Notice error>{error}</Notice>}
    </section>
    {open && <Dialog title="Установить обновление?" busy={busy} onClose={() => setOpen(false)} footer={<><Button secondary disabled={busy} onClick={() => setOpen(false)}>Не сейчас</Button><Button disabled={busy || !data.install_supported} onClick={install}>{busy && <Spinner />}Установить обновление</Button></>}>
      <p>Версия <code>{data.current_commit?.slice(0, 7)}</code> → <code>{data.latest_commit?.slice(0, 7)}</code></p>
      <p className="update-changelog">{data.message}</p>
      <a className="text-link" href={data.commit_url} target="_blank" rel="noreferrer">Изменения на GitHub <ExternalLink size={14} /></a>
      <p>Приложение приостановит автоматизацию, сохранит базу, ключ и сессии, установит зависимости и перезапустится. Во время установки панель будет недоступна несколько минут.</p>
      <Notice>После обновления автоматизация останется приостановленной. Проверьте работу перед возобновлением торговли.</Notice>
      {!data.install_supported && <Notice>{data.install_reason}</Notice>}
      {error && <Notice error>{error}</Notice>}
    </Dialog>}
  </>;
}

export function UpdateSettings() {
  const update = useUpdates();
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const data = update.data;
  async function check() {
    setBusy(true); setError("");
    try { const result = await api("/updates/check", "POST"); await waitTask(result.task_id); await update.mutate(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <Panel title="Обновления приложения" subtitle="Проверяем GitHub при запуске сервера и каждые 15 минут. Установка — только по вашему решению." action={<Button secondary disabled={busy || active.includes(data?.progress?.state)} onClick={check}>{busy ? <Spinner /> : <RefreshCw size={15} />}Проверить обновления</Button>}>
    <div className="form-pad update-settings"><p>Установленный коммит: <code>{data?.current_commit?.slice(0, 7) || "Не определён (ZIP-копия)"}</code></p><p>Последняя проверка: {date(data?.checked_at)}</p><p className="muted">Время проверок — UTC+3.</p>
      {data?.available && <p>Новая версия: <a href={data.commit_url} target="_blank" rel="noreferrer">{data.latest_commit?.slice(0, 7)}</a> — {data.message?.split("\n")[0]}{data.dismissed && <Button secondary onClick={async () => { await api("/updates/dismiss", "POST", { commit: "0".repeat(40) }); await update.mutate(); }}>Показать обновление</Button>}</p>}
      {!data?.available && data?.checked_at && !data.last_error && <p>Новых обновлений нет.</p>}
      {data && !data.install_supported && <Notice>{data.install_reason}</Notice>}
      {(error || data?.last_error) && <Notice error>{error || data.last_error}</Notice>}
      <a className="text-link" href="/guide.html" target="_blank" rel="noreferrer">Инструкция по обновлению и восстановлению <ExternalLink size={14} /></a>
    </div>
  </Panel>;
}
