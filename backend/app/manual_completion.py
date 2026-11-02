"""Silent dashboard-only closure. No marketplace, message, gift or ledger calls."""
import time
from decimal import Decimal
from sqlalchemy import select,update,insert
from .db import Entity,Task,uid


def complete(db,oid):
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        records={(r.kind,r.id):dict(r.data) for r in conn.execute(select(Entity))}
        order=records.get(("order",oid))
        if not order:raise ValueError("Заказ не найден")
        if order.get("completed_manually"):conn.commit();return {"completed":True,"silent":True}
        gid=order.get("funding_grant_id")
        grant=records.get(("grant",gid)) if gid else None
        cid=order.get("catalog_credit_id") or order.get("catalog_root_credit_id") or order.get("catalog_topup_credit_id")
        wallet=records.get(("catalog_credit_payment",cid)) if cid else None
        if order.get("stage")!="MANUAL" and not (wallet and wallet.get("state")=="REVIEW"):
            raise ValueError("Завершить вручную можно только заказ в ручной обработке")
        linked={id for (kind,id),value in records.items() if kind=="order" and (id==oid or (cid and cid in (value.get("catalog_credit_id"),value.get("catalog_root_credit_id"),value.get("catalog_topup_credit_id"))) or (gid and (value.get("funding_grant_id")==gid or id==(grant or {}).get("root_order_id"))))}
        ops={id for (kind,id),value in records.items() if kind=="operation" and value.get("order_id") in linked}
        messages={id for (kind,id),value in records.items() if kind in {"catalog_message","buyer_message","grant_message"} and (value.get("order_id") in linked or (cid and value.get("credit_id")==cid) or (gid and value.get("grant_id")==gid))}
        if any(value.get("state")=="SUBMITTED" for (kind,id),value in records.items() if kind=="operation" and id in ops):raise ValueError("Дождитесь завершения отправленного запроса")
        relevant=[]
        for task in conn.execute(select(Task)):
            p=task.payload or {}
            if p.get("order_id") in linked or p.get("operation_id") in ops or p.get("message_id") in messages or (cid and p.get("credit_id")==cid) or (gid and p.get("grant_id")==gid):
                if task.state=="RUNNING":raise ValueError("Заказ сейчас обрабатывается. Дождитесь завершения операции")
                relevant.append(task.id)
        stamp=time.time()
        def put(kind,id,value):
            if (kind,id) in records:conn.execute(update(Entity).where(Entity.kind==kind,Entity.id==id).values(data=value,updated=stamp))
            else:conn.execute(insert(Entity).values(kind=kind,id=id,data=value,created=stamp,updated=stamp))
        for id in linked:
            value=records[("order",id)]
            put("order",id,{**value,"stage":"COMPLETE","completed_at":stamp,"activity_at":stamp,"completed_manually":True,"manual_hold":True,"reason":"Завершён продавцом в панели без закупки, передачи и сообщений"})
            put("processed",id,{"source":"owner_silent_completion"})
        for id in ops:
            value=records[("operation",id)]
            if value.get("state") in {"QUEUED","RETRY_PENDING","VERIFYING"}:
                put("operation",id,{**value,"state":"UNKNOWN" if value.get("submitted_at") else "CANCELLED","stopped_by_owner":True})
        for (kind,id),value in records.items():
            if id in messages and kind in {"catalog_message","buyer_message","grant_message"} and value.get("state")=="QUEUED":put(kind,id,{**value,"state":"CANCELLED","reason":"Заказ завершён без сообщений"})
        if relevant:conn.execute(update(Task).where(Task.id.in_(relevant),Task.state=="READY").values(state="CANCELLED",dedupe=None,lease=None))
        if grant:put("grant",gid,{**grant,"state":"CANCELLED","closed_manually":True,"notice":"Заказ завершён продавцом"})
        if wallet:
            used=sum((Decimal(str(p["price_rub"])) for p in wallet.get("purchases",[])),Decimal(0))
            put("catalog_credit_payment",cid,{**wallet,"state":"DELETED","closed_manually":True,"closed_at":stamp,"reservation_rub":"0","adjustment_rub":str(used-Decimal(str(wallet.get("paid_rub",0)))),"notice":"Заказ завершён продавцом"})
            put("catalog_credit_event",uid(),{"payment_id":cid,"action":"OWNER_SILENT_COMPLETION","order_id":oid,"at":stamp})
        put("event",uid(),{"action":"owner_silent_completion","title":"Заказ завершён продавцом без внешних действий","order_id":oid,"level":"INFO"})
        conn.commit()
    return {"completed":True,"silent":True}
