"use client";
import { useState } from "react";
import useSWR from "swr";
import { api, type Row } from "../lib/api";
import { Badge, Button, Dialog, Empty, Field, Notice, Panel, Spinner, date, money } from "./ui";

const eventNames:Record<string,string>={CREATED:"Код создан",PROMOTIONAL_PAID:"Оплачен рекламный лот",MESSAGE_SENT:"Сообщение отправлено",MESSAGE_UNKNOWN:"Сообщение требует проверки",ACTIVATED:"Вход по коду",GIFT_SELECTED:"Подарок выбран",TOPUP_CREATED:"Доплата зафиксирована",TOPUP_CONFIRMED:"Доплата подтверждена Playerok",TOPUP_PUBLISHED:"Лот доплаты опубликован",FULFILLMENT_QUEUED:"Средства зарезервированы",FULFILLMENT_STARTED:"Закупка и выдача запущены",GIFT_DELIVERED:"Передача NFT подтверждена",FULFILLMENT_STOPPED:"Выдача остановлена до закупки",REVIEW_REQUIRED:"Нужна сверка",PAYMENT_REVIEW:"Доплата требует сверки/возврата",TOPUP_EXPIRED:"Срок доплаты истёк",BALANCE_CHANGED:"Баланс изменён владельцем",DELETED:"Код удалён"};

export default function CatalogCodes() {
  const [filter,setFilter]=useState("all"),[amount,setAmount]=useState("1000"),[recipient,setRecipient]=useState(""),[lotId,setLotId]=useState("");
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState(""),[created,setCreated]=useState<Row|null>(null);
  const [dialog,setDialog]=useState<{mode:"balance"|"delete"|"logs";id:string}|null>(null),[balance,setBalance]=useState(""),[note,setNote]=useState("");
  const codes=useSWR(`/catalog-codes?status=${filter}`,path=>api(path),{refreshInterval:5000});
  const lots=useSWR("/lots",path=>api(path));
  const logs=useSWR(dialog?.mode==="logs"?`/catalog-codes/${dialog.id}/events`:null,path=>api(path),{refreshInterval:5000});
  const rows:Row[]=codes.data?.items || [];
  const selected=dialog?rows.find(r=>r.id===dialog.id):null;
  const nftLots:Row[]=(lots.data?.items || []).filter((l:Row)=>l.pair_id && l.delivery_mode!=="manual");
  async function run(action:()=>Promise<void>){setBusy(true);setError("");setMessage("");try{await action();}catch(e){setError(e instanceof Error?e.message:"Не удалось выполнить действие");}finally{setBusy(false);}}
  async function copy(text:string){try{await navigator.clipboard.writeText(text);setMessage("Скопировано");}catch{setError("Не удалось скопировать. Выделите код вручную.");}}
  function open(mode:"balance"|"delete"|"logs",row:Row){setError("");setDialog({mode,id:row.id});setBalance(String(row.balance_rub));setNote("");}
  return <Panel title="Коды каталога" subtitle="Реальные коды: сумма, зачисленная вручную владельцем, используется для закупки и выдачи NFT.">
    <div className="form-pad">
      <form onSubmit={e=>{e.preventDefault();run(async()=>{
        const result=await api<Row>("/catalog-codes","POST",{amount_rub:amount,recipient:recipient.trim().replace(/^@/,""),lot_id:lotId || null});
        setCreated(result);setMessage("Код создан");await codes.mutate();
      });}}>
        <div className="form-grid">
          <Field label="Сумма кода, ₽" help="Ручное зачисление в целых рублях: эта сумма используется для реальных закупок NFT."><input type="number" min="1" max="10000000" step="1" value={amount} onChange={e=>setAmount(e.target.value)} required disabled={busy}/></Field>
          <Field label="Telegram-получатель кода"><input value={recipient} onChange={e=>setRecipient(e.target.value)} placeholder="@username" required maxLength={33} disabled={busy}/></Field>
          <Field label="Коллекция из лота" help="Необязательно. Без выбора ссылка откроет главную каталога с применённым балансом."><select value={lotId} onChange={e=>setLotId(e.target.value)} disabled={busy}><option value="">Главная каталога — без привязки к лоту</option>{nftLots.map(l=><option key={l.id} value={l.id}>{l.title}</option>)}</select></Field>
        </div>
        <Button type="submit" disabled={busy || !recipient.trim() || !amount}>{busy?<Spinner/>:null}Создать код</Button>
      </form>
      {created && <div className="panel-note"><strong>Новый код: <code>{created.code}</code></strong><Button secondary onClick={()=>copy(created.code)}>Копировать код</Button><Button secondary onClick={()=>copy(created.url)}>Копировать ссылку</Button></div>}
      {message && <p role="status">{message}</p>}
      {error && !dialog && <Notice error>{error}</Notice>}
    </div>
    <div className="table-toolbar"><select aria-label="Состояние кодов" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">Все ({codes.data?.counts?.all ?? 0})</option><option value="valid">Действительные ({codes.data?.counts?.valid ?? 0})</option><option value="invalid">Недействительные ({codes.data?.counts?.invalid ?? 0})</option></select><Button secondary disabled={busy} onClick={()=>codes.mutate()}>Обновить коды</Button></div>
    {codes.error?<div className="form-pad"><Notice error>{codes.error.message}</Notice></div>:!codes.data?<div className="form-pad"><Spinner/></div>:rows.length?<div className="table-scroll"><table><thead><tr><th>Код</th><th>Состояние</th><th>Получатель</th><th>Оплачено / баланс</th><th>Создан</th><th>Управление</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}>
      <td><strong><code>{row.code}</code></strong><small>{row.collection || "Все коллекции"}</small></td>
      <td><Badge tone={row.valid?"green":"neutral"}>{row.state==="DELETED"?"Удалён":row.state==="EXECUTING"?"Закупка и выдача":row.state==="REVIEW"?"Нужна сверка":row.locked?"Ожидает доплату":row.valid?"Действителен":"Баланс исчерпан"}</Badge>{row.notice && <small>{row.notice}</small>}</td>
      <td>@{row.recipient}</td><td>{money(row.paid_rub)}<small>Баланс: {money(row.balance_rub)}</small></td><td>{date(row.created_at)}</td>
      <td><div className="button-group wrap"><Button secondary disabled={!row.valid} onClick={()=>copy(row.url)}>Ссылка</Button><Button secondary disabled={busy || row.state==="DELETED" || row.locked} onClick={()=>open("balance",row)}>Баланс</Button><Button secondary onClick={()=>open("logs",row)}>Логи</Button><Button danger disabled={busy || ["DELETED","EXECUTING"].includes(row.state)} onClick={()=>open("delete",row)}>Удалить</Button></div></td>
    </tr>)}</tbody></table></div>:<Empty text="Кодов пока нет" detail="Создайте код выше или выберите другой фильтр."/>}
    {dialog && <Dialog title={dialog.mode==="balance"?"Изменить баланс кода":dialog.mode==="delete"?"Удалить код?":"Логи кода"} onClose={()=>setDialog(null)} busy={busy} footer={<Button secondary disabled={busy} onClick={()=>setDialog(null)}>Закрыть</Button>}>
      {selected && <p><strong>{selected.code}</strong> · @{selected.recipient}</p>}
      {error && <Notice error>{error}</Notice>}
      {dialog.mode==="balance" && <form onSubmit={e=>{e.preventDefault();run(async()=>{await api(`/catalog-codes/${dialog.id}/balance`,"PATCH",{balance_rub:balance,note});await codes.mutate();setDialog(null);setMessage("Баланс обновлён");});}}>
        <Field label="Новый доступный баланс, ₽" help="Это остаток для следующих подарков. История оплат и покупок сохраняется."><input type="number" min="0" max="10000000" step="1" value={balance} onChange={e=>setBalance(e.target.value)} required disabled={busy}/></Field>
        <Field label="Комментарий изменения"><textarea value={note} onChange={e=>setNote(e.target.value)} maxLength={500} rows={2} disabled={busy}/></Field><Button type="submit" disabled={busy || balance===""}>Сохранить баланс</Button>
      </form>}
      {dialog.mode==="delete" && <><p>Код перестанет работать. Баланс, покупки и журнал останутся в истории недействительных кодов. Удаление не возвращает оплаты и не отменяет уже отправленные запросы.</p><Button danger disabled={busy} onClick={()=>run(async()=>{await api(`/catalog-codes/${dialog.id}`,"DELETE");await codes.mutate();setDialog(null);setMessage("Код удалён");})}>Удалить и заблокировать</Button></>}
      {dialog.mode==="logs" && (logs.error?<Notice error>{logs.error.message}</Notice>:!logs.data?<Spinner/>:<div className="events-list full">{logs.data.items.map((event:Row)=><div className="event-item" key={event.id}><div><strong>{eventNames[event.action] || event.action}</strong>{event.before_rub!==undefined && <small>Баланс: {money(event.before_rub)} → {money(event.after_rub)}</small>}{event.gift_id && <small>NFT: {event.gift_id}</small>}{event.topup_rub && <small>Доплата: {money(event.topup_rub)}</small>}{event.note && <small>{event.note}</small>}</div><time>{date(event.at)}</time></div>)}</div>)}
    </Dialog>}
  </Panel>;
}
