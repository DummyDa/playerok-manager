"""Owner-confirmed delivery of a review order; never buys or transfers an NFT."""
import time
from sqlalchemy import select, update
from .db import Entity, Task
from .pricing import D
from .safety import gate


def target(db, oid):
    order=db.get("order",oid)
    if not order:raise ValueError("Заказ не найден")
    cid=order.get("catalog_credit_id") or order.get("catalog_root_credit_id") or order.get("catalog_topup_credit_id")
    wallet=db.get("catalog_credit_payment",cid) if cid else None
    if wallet and not order.get("catalog_credit_id"):
        order=db.get("order",wallet.get("fulfillment_order_id"))
    if not order or not wallet or not wallet.get("selected"):raise ValueError("У заказа нет выбранного подарка для подтверждения выдачи")
    return order,wallet


def prepare(db, oid, purchase_rub=None):
    order,wallet=target(db,oid)
    if order.get("manual_delivery_receipt"):return order["id"]
    if order.get("stage")!="MANUAL" or wallet.get("state") not in {"REVIEW","EXECUTING"}:
        raise ValueError("Заказ не ожидает ручного завершения")
    if purchase_rub is not None and D(purchase_rub)<=0:raise ValueError("Укажите фактическую стоимость закупки")
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        table=Entity.__table__
        row=conn.execute(select(table.c.data).where(table.c.kind=="order",table.c.id==order["id"])).scalar_one()
        if row.get("stage")!="MANUAL":raise ValueError("Заказ изменился. Обновите страницу")
        ops=conn.execute(select(table.c.id,table.c.data).where(table.c.kind=="operation")).all()
        linked=[op for op in ops if op.data.get("order_id")==order["id"]]
        if any(op.data.get("state")=="SUBMITTED" for op in linked):raise ValueError("Дождитесь завершения отправленного запроса")
        op_ids={op.id for op in linked}
        tasks=[t for t in conn.execute(select(Task)).all() if t.payload.get("order_id")==order["id"] or t.payload.get("operation_id") in op_ids]
        if any(t.state=="RUNNING" for t in tasks):raise ValueError("Заказ сейчас обрабатывается. Дождитесь завершения задачи")
        for op in linked:
            if op.data.get("state") in {"QUEUED","RETRY_PENDING","VERIFYING"}:
                data={**op.data,"state":"UNKNOWN" if op.data.get("submitted_at") else "CANCELLED","stopped_by_owner":True}
                conn.execute(table.update().where(table.c.kind=="operation",table.c.id==op.id).values(data=data,updated=time.time()))
        ids=[t.id for t in tasks if t.state=="READY"]
        if ids:conn.execute(update(Task).where(Task.id.in_(ids)).values(state="CANCELLED",dedupe=None,lease=None))
        values={**row,"manual_hold":True,"owner_delivery_requested":True}
        if purchase_rub is not None:values["owner_purchase_rub"]=str(D(purchase_rub))
        conn.execute(table.update().where(table.c.kind=="order",table.c.id==order["id"]).values(data=values,updated=time.time()))
        conn.commit()
    return order["id"]


def confirm(service, oid):
    from . import catalog_credit, catalog_premium
    from .catalog_pricing import context
    from .integrations.clients import Playerok
    db=service.db;gate(db)
    order,wallet=target(db,oid)
    if order.get("manual_delivery_receipt"):
        if order.get("stage")!="COMPLETE":service.process_order(order["id"])
        return {"confirmed":True,"operation_id":order["id"]+":mark_sent"}
    if not order.get("owner_delivery_requested") or not order.get("manual_hold"):raise ValueError("Сначала подтвердите ручную выдачу в панели")
    gift=wallet["selected"];slug=order.get("gift_slug") or gift.get("slug");recipient=order.get("recipient") or wallet.get("recipient")
    if not slug or not recipient or (order.get("gift_slug") and order["gift_slug"]!=gift.get("slug")):
        raise ValueError("Не подтверждены точный подарок и получатель")
    client=Playerok(db)
    for receipt in catalog_credit.receipts(db,wallet["id"]):
        fresh=client.orders(deal_id=receipt["id"])[0]
        if fresh.get("buyer_id")!=receipt.get("buyer_id") or fresh["status"] not in catalog_credit.PAID or D(fresh["sale_rub"])!=D(receipt["amount_rub"]):
            raise ValueError("Оплата связанной сделки больше не подтверждена")
    expenses=[e for e in db.list("ledger",100000) if e.get("order_id")==order["id"] and e.get("type")=="purchase"]
    cost=sum((D(e["amount_rub"]) for e in expenses),D(0))
    if not expenses:
        if order.get("owner_purchase_rub") is None:raise ValueError("Укажите фактическую закупку: покупка системой не была подтверждена")
        cost=D(order["owner_purchase_rub"])
    c=context(db)
    catalog_premium.refresh(service,limit=12,credit_id=wallet["id"])
    previous=order.get("snapshot") or {}
    base={**previous,"purchase_rub":str(cost),"ton_rub":previous.get("ton_rub") or str(c["rate"]),"transfer_rub":previous.get("transfer_rub") or str(c["transfer"])}
    snapshot=catalog_credit.adjust_snapshot(db,wallet["id"],base,previous.get("fee_profile") or c["profile"])
    if not expenses:service.ledger(order["id"]+":owner-purchase","purchase",cost,order["lot_id"],order["id"],source="owner_attestation")
    receipt={"at":time.time(),"slug":slug,"recipient_id":order.get("recipient_id"),"recipient":recipient,"source":"owner_manual_review"}
    db.put("order",order["id"],{"stage":"TRANSFERRED","gift_slug":slug,"recipient":recipient,"snapshot":snapshot,
        "manual_delivery_receipt":receipt,"manual_hold":False,"reason":"Подарок передан продавцом вручную"})
    # Existing catalog settlement performs the SENT requests, customer reply,
    # revenue and fees using their durable single-attempt keys.
    service.process_order(order["id"])
    return {"confirmed":True,"operation_id":order["id"]+":mark_sent"}
