"use client";
import { api,type Row } from "../lib/api";
import type {Actions} from "./Dashboard";
import {Button,Dialog,Notice,State,date,money} from "./ui";
import ManualDeliveryButton from "./ManualDeliveryButton";

export default function CatalogOrder({order,onClose,refresh,act,busy}:{order:Row;onClose:()=>void;refresh:()=>Promise<unknown>} & Actions){
  const payments:Row[]=order.payments||[],deliveries:Row[]=order.deliveries||[];
  return <Dialog title="Заказ каталога" onClose={onClose} busy={busy} footer={<Button secondary onClick={onClose}>Закрыть</Button>}>
    <div className="order-title"><strong>{order.title}</strong><small>{order.id}</small><State value={order.stage}/></div>
    {order.reason&&<Notice>{order.reason}</Notice>}
    <div className="summary-grid">
      <div><small>Подтверждённые оплаты</small><strong>{money(order.paid_rub)}</strong></div>
      <div><small>Баланс заказа</small><strong>{money(order.balance_rub)}</strong></div>
      <div><small>Зарезервировано</small><strong>{money(order.reserved_rub)}</strong></div>
      <div><small>Подтверждённые возвраты</small><strong>{money(order.refunded_rub)}</strong></div>
      <div><small>Получатель текущего выбора</small><strong>{order.recipient?"@"+order.recipient:"Не указан"}</strong></div>
      <div><small>Подарков выдано</small><strong>{order.delivery_count}</strong></div>
    </div>
    {(Number(order.manual_credit_rub)!==0||Number(order.adjustment_rub)!==0)&&<Notice>Ручное зачисление: {money(order.manual_credit_rub)}. Корректировки владельца: {money(order.adjustment_rub)}. Эти суммы не являются подтверждёнными платежами Playerok.</Notice>}
    {order.selected&&<p><strong>Текущий выбор: {order.selected.slug}</strong> · {money(order.selected_price)}</p>}
    <h3>Оплаты и доплаты</h3>
    {payments.length?<div className="table-scroll"><table><thead><tr><th>Платёж</th><th>Сумма</th><th>Состояние</th></tr></thead><tbody>{payments.map(p=><tr key={p.id}><td><a href={"https://playerok.com/deal/"+encodeURIComponent(p.id)} target="_blank" rel="noreferrer">{p.kind==="ORIGINAL"?"Исходная оплата":"Доплата"}</a><small>{p.id}</small></td><td>{money(p.amount_rub)}</td><td>{p.accepted===false?"Не зачислена — нужна сверка":p.status==="ROLLED_BACK"?"Возвращено":p.status==="SENT"?"Выдача отмечена на Playerok":p.status==="CONFIRMED"||p.status==="CONFIRMED_AUTOMATICALLY"?"Получение подтверждено":p.status==="PAID"?"Оплачено":p.status}</td></tr>)}</tbody></table></div>:<p className="muted">Оплат Playerok нет. Используется ручное зачисление.</p>}
    <h3>Закупка и выдача</h3>
    {deliveries.length?<div className="table-scroll"><table><thead><tr><th>NFT / получатель</th><th>Сумма</th><th>Этап</th><th/></tr></thead><tbody>{deliveries.map(d=><tr key={d.id}><td><strong>{d.gift_slug||d.title}</strong><small>{d.recipient?"@"+d.recipient:"Получатель не указан"}</small>{d.reason&&<small>{d.reason}</small>}</td><td>{money(d.sale_rub)}{d.profit_rub!=null&&<small>{d.profit_actual?"Факт":"Расчёт"}: {money(d.profit_rub)}</small>}</td><td><State value={d.stage}/><small>{date(d.updated_at)}</small></td><td><ManualDeliveryButton order={d} refresh={refresh} act={act} busy={busy}/>{d.retry_allowed&&<Button secondary disabled={busy} onClick={()=>act(async()=>{await api(`/orders/${encodeURIComponent(d.id)}/process`,"POST");await refresh();},"Выдача поставлена на проверку")}>Повторить выдачу</Button>}</td></tr>)}</tbody></table></div>:<p className="muted">Покупатель ещё не подтвердил выбор подарка.</p>}
    <h3>Фактические расходы и результат</h3>
    <div className="summary-grid">{[["Выручка выданных подарков","revenue"],["Закупки","purchase"],["Передачи","transfer"],["Комиссии","fees"],["Премиум","promotion"]].map(([label,key])=><div key={key}><small>{label}</small><strong>{money(order.totals?.[key])}</strong></div>)}<div><small>Прибыль учтённых выдач</small><strong>{order.profit_rub==null?"Ещё не зафиксирована":money(order.profit_rub)}</strong></div></div>
    <p className="muted">Расходы включают начатые закупки. Прибыль считается только по выдачам с зафиксированной выручкой; остаток баланса и доплаты повторно в выручку не добавляются.</p>
  </Dialog>;
}
