from __future__ import annotations
import time
from .db import Entity, uid
from .safety import gate

def new_operation(db, action, payload, expected=None):
    return db.put("operation",uid(),{"action":action,"state":"PREVIEW","payload":payload,"expected":expected or {},"expires":time.time()+120})

def submit(db, id):
    gate(db)
    with db.tx() as s:
        # SQLite serializes this UPDATE; concurrent clicks cannot consume the same preview.
        from sqlalchemy import update
        op=s.get(Entity,("operation",id))
        if not op: raise ValueError("Расчёт не найден")
        data=dict(op.data)
        if data.get("state")!="PREVIEW": return {"id":id,**data}
        if data.get("expires",0)<time.time(): raise ValueError("Расчёт устарел; получите новый")
        new={**data,"state":"QUEUED"}
        affected=s.execute(update(Entity).where(Entity.kind=="operation",Entity.id==id,Entity.data==op.data).values(data=new,updated=time.time())).rowcount
        # A concurrent confirmation already consumed this same preview. Both
        # callers converge on the same operation and deduplicated queue entry.
    db.enqueue("execute",{"operation_id":id},key="execute:"+id)
    return db.get("operation",id)

def recover(db):
    from sqlalchemy import select
    from .db import Task
    with db.tx() as s:
        for task in s.scalars(select(Task).where(Task.state=="RUNNING")):
            if task.kind=="telegram_auth":
                task.state="FAILED";task.dedupe=None;task.error="Шаг входа прерван перезапуском; автоматический повтор отключён"
            else:task.state="READY"
            task.lease=None
    from .telegram_login import status,secret_name
    login=status(db)
    if login["state"] in {"VERIFYING","REQUESTING_CODE"}:
        db.forget_secret(secret_name(login["flow_id"]))
        db.put("telegram_login","current",{"state":"ERROR","error":"Вход прерван перезапуском. Запросите новый код."})
    for op in db.list("operation",100000):
        if op.get("state")=="SUBMITTED":
            db.put("operation",op["id"],{"state":"UNKNOWN","error":"Процесс прерван после отправки. Нужна сверка результата."})
            if op.get("order_id"): db.put("order",op["order_id"],{"stage":"MANUAL","reason":"Неизвестен результат денежной операции"})
        elif op.get("state")=="QUEUED": db.enqueue("execute",{"operation_id":op["id"]},key="execute:"+op["id"])
    for message in db.list("buyer_message",100000):
        if message.get("state")=="SUBMITTED":
            db.put("buyer_message",message["id"],{"state":"UNKNOWN","error":"Отправка прервана перезапуском; автоматического повтора нет"})
        elif message.get("state")=="QUEUED":
            db.enqueue("buyer_message",{"message_id":message["id"]},key="buyer_message:"+message["id"])
def order_retry_reason(order, operations, has_spend=False):
    if not order or order.get("source")=="legacy" or order.get("cancel_requested"):
        return "Импортированный или отменённый заказ нельзя повторить"
    if order.get("stage") not in {"MANUAL","DISCOVERED","AWAITING_ENABLE","WAITING_DATA","CHECK_PRICE","PURCHASED","WITHDRAWN","TRANSFERRED"}:
        return "Заказ завершён, отменён или уже исполняется"
    if any(op.get("state") in {"QUEUED","SUBMITTED","UNKNOWN","BROWSER_WAIT"} for op in operations):
        return "Предыдущая операция выполняется или требует сверки"
    if any(op.get("state")=="FAILED" and op.get("submitted_at") for op in operations):
        return "Денежный запрос уже отправлялся; сначала нужна сверка"
    if order.get("stage")=="MANUAL" or order.get("manual_hold"):
        if (order.get("mrkt_gift_id") or order.get("withdraw_at") or has_spend
                or any(op.get("state")=="COMPLETE" for op in operations)):
            return "Заказ уже прошёл денежный этап; сначала нужна сверка"
    return None
