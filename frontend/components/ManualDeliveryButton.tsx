"use client";
import {useState} from "react";
import {api,waitTask,waitOperation,type Row} from "../lib/api";
import type {Actions} from "./Dashboard";
import {Button,Dialog,Notice} from "./ui";

export default function ManualDeliveryButton({order,refresh,act,busy}:{order:Row;refresh:()=>Promise<unknown>} & Actions){
  const [confirm,setConfirm]=useState(false);
  if(order.stage==="MANUAL")return <><Button secondary disabled={busy} onClick={()=>setConfirm(true)}>Завершить вручную</Button>{confirm&&<Dialog title="Завершить заказ без отправки?" busy={busy} onClose={()=>setConfirm(false)} footer={<><Button secondary disabled={busy} onClick={()=>setConfirm(false)}>Назад</Button><Button disabled={busy} onClick={()=>act(async()=>{await api(`/orders/${encodeURIComponent(order.id)}/complete-manually`,"POST");setConfirm(false);await refresh();},"Заказ завершён в панели без отправки покупателю")}>Завершить без отправки</Button></>}><Notice>Заказ и связанные оплаты будут помечены завершёнными только в панели. Код закроется. Закупка, передача NFT, изменение статуса на Playerok и сообщения покупателю не запустятся.</Notice></Dialog>}</>;
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
