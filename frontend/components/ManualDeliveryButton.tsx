"use client";
import {useState} from "react";
import {api,waitTask,waitOperation,type Row} from "../lib/api";
import type {Actions} from "./Dashboard";
import {Button,Dialog,Notice,Field} from "./ui";

export default function ManualDeliveryButton({order,refresh,act,busy}:{order:Row;refresh:()=>Promise<unknown>} & Actions){
  const [confirm,setConfirm]=useState(false);
  const [purchase,setPurchase]=useState("");
  if(order.stage==="MANUAL" && !order.catalog_credit_id && !order.catalog_group && !order.catalog_root_credit_id && !order.catalog_topup_credit_id)return <><Button secondary disabled={busy} onClick={()=>setConfirm(true)}>Закрыть без выдачи</Button>{confirm&&<Dialog title="Закрыть заказ без выдачи?" busy={busy} onClose={()=>setConfirm(false)} footer={<Button disabled={busy} onClick={()=>act(async()=>{await api(`/orders/${encodeURIComponent(order.id)}/complete-silently`,"POST");setConfirm(false);await refresh();},"Заказ закрыт в панели без выдачи и сообщений")}>Закрыть без выдачи</Button>}><Notice>Этот заказ будет закрыт только в панели. Подтверждения на Playerok, выдачи и сообщений покупателю не будет.</Notice></Dialog>}</>;
  if(order.stage==="MANUAL")return <><Button secondary disabled={busy} onClick={()=>setConfirm(true)}>Подарок передан</Button>{confirm&&<Dialog title="Подтвердить ручную выдачу" busy={busy} onClose={()=>setConfirm(false)} footer={<><Button secondary disabled={busy} onClick={()=>setConfirm(false)}>Назад</Button><Button disabled={busy} onClick={()=>act(async()=>{const task=await api(`/orders/${encodeURIComponent(order.id)}/complete-manually`,"POST",purchase?{purchase_rub:purchase}:{});try{const result=await waitTask(task.task_id);if(result.operation_id)await waitOperation(result.operation_id);setConfirm(false);}finally{await refresh();}},"Выдача завершена. Сделки отмечены отправленными, сообщение покупателю поставлено в очередь.")}>Да, подарок передан</Button></>}><Notice>Подтвердите, что уже передали выбранный подарок покупателю. Бот отметит основную сделку и доплаты отправленными на Playerok, отправит сообщение и рассчитает прибыль. Повторной закупки или передачи не будет.</Notice><Field label="Закупка вручную, ₽" help="Если купили подарок самостоятельно, укажите фактическую стоимость. Если закупка системой уже учтена, оставьте поле пустым."><input type="number" min="0.01" step="0.01" value={purchase} onChange={e=>setPurchase(e.target.value)}/></Field></Dialog>}</>;
  if(!order.manual_delivery_allowed)return null;
  return <><Button disabled={busy} onClick={()=>setConfirm(true)}>Выдан</Button>{confirm&&<Dialog title="Подтвердить ручную выдачу" busy={busy} onClose={()=>setConfirm(false)} footer={<><Button secondary disabled={busy} onClick={()=>setConfirm(false)}>Назад</Button><Button disabled={busy} onClick={()=>act(async()=>{
    const task=await api(`/orders/${encodeURIComponent(order.id)}/manual-delivered`,"POST");
    try{
      const result=await waitTask(task.task_id);
      if(result.operation_id)await waitOperation(result.operation_id);
      setConfirm(false);
    }finally{await refresh();}
  },"Выдача завершена. Связанные сделки отмечены отправленными.")}>Да, подарок передан</Button></>}>
    <p>Вы уже передали <strong>{order.gift_slug}</strong> получателю <strong>@{String(order.recipient||"").replace(/^@/,"")}</strong>?</p>
    <Notice>После подтверждения бот спишет баланс заказа, закроет код, отметит основную сделку и доплаты отправленными на Playerok и отправит покупателю сообщение. Повторной передачи подарка не будет.</Notice>
  </Dialog>}</>;
}
