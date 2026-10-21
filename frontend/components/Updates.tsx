"use client";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { ArrowUpCircle, ExternalLink, RefreshCw } from "lucide-react";
import { api, waitTask } from "../lib/api";
import { Button, Dialog, Notice, Panel, Spinner, date } from "./ui";

const active = ["QUEUED", "FETCHING", "STOPPING", "BACKUP", "INSTALLING", "RESTARTING", "ROLLING_BACK"];
const useUpdates = () => useSWR<any>("/updates", api, { refreshInterval: 5000, shouldRetryOnError: false });

export function UpdateBanner() {
  const update = useUpdates();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [openedVersion, setOpenedVersion] = useState("");
  const data = update.data;
  const completion = data?.progress?.state === "COMPLETE" ? `${data.progress.installed_commit || data.progress.target}:${data.progress.updated_at}` : "";
  useEffect(() => {
    if (!completion) return;
    try { setOpenedVersion(localStorage.getItem("playerok.update-opened") || sessionStorage.getItem("playerok.update-opened") || ""); } catch {}
  }, [completion]);
  if (!data) return null;
  const running = active.includes(data.progress?.state);
  const recentResult = ["COMPLETE", "FAILED"].includes(data.progress?.state) && (!completion || openedVersion !== completion) && Date.now() / 1000 - data.progress.updated_at < 1800;
  if ((!data.available || data.dismissed) && !running && !recentResult) return null;
  async function install() {
    setBusy(true); setError("");
    try { await api("/updates/install", "POST", { commit: data.latest_commit }); setOpen(false); await update.mutate(); }
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
      <div className="update-copy"><strong>{running ? "Устанавливаем обновление" : recentResult ? data.progress.state === "COMPLETE" ? "Приложение обновлено" : "Обновление не установлено" : "Доступно обновление Playerok Manager"}</strong><span>{running || recentResult ? data.progress.message : (data.message || "В GitHub появилась новая версия").split("\n")[0]}</span></div>
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
  return <Panel title="Обновления приложения" subtitle="Проверяем GitHub каждые 15 минут. Установка — только по вашему решению." action={<Button secondary disabled={busy || active.includes(data?.progress?.state)} onClick={check}>{busy ? <Spinner /> : <RefreshCw size={15} />}Проверить обновления</Button>}>
    <div className="form-pad update-settings"><p>Установленный коммит: <code>{data?.current_commit?.slice(0, 7) || "Не определён (ZIP-копия)"}</code></p><p>Последняя проверка: {date(data?.checked_at)}</p><p className="muted">Время проверок — UTC+3.</p>
      {data?.available && <p>Новая версия: <a href={data.commit_url} target="_blank" rel="noreferrer">{data.latest_commit?.slice(0, 7)}</a> — {data.message?.split("\n")[0]}{data.dismissed && <Button secondary onClick={async () => { await api("/updates/dismiss", "POST", { commit: "0".repeat(40) }); await update.mutate(); }}>Показать обновление</Button>}</p>}
      {!data?.available && data?.checked_at && !data.last_error && <p>Новых обновлений нет.</p>}
      {data && !data.install_supported && <Notice>{data.install_reason}</Notice>}
      {(error || data?.last_error) && <Notice error>{error || data.last_error}</Notice>}
      <a className="text-link" href="/guide.html" target="_blank" rel="noreferrer">Инструкция по обновлению и восстановлению <ExternalLink size={14} /></a>
    </div>
  </Panel>;
}
