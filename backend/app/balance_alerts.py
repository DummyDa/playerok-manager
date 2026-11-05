"""Owner balance alerts and safe retry of purchases that were never submitted."""
import time
from html import escape

from sqlalchemy import select
from .db import Entity,Task,uid
from .pricing import D

ACCEPTED="Заказ принят. Готовим подарок к выдаче. Повторно оплачивать не нужно."


def observe(db,balance):
    ton,stars=D(balance["mrkt_ton"]),D(balance["stars"])
    if ton<0 or stars<0:raise ValueError("Некорректный баланс")
    enabled=db.settings().get("notifications_enabled")
    with db.tx() as session:
        row=session.get(Entity,("balance_watch","mrkt"))
        previous=dict(row.data) if row else {}
        low={"ton":ton<15,"stars":stars<=50}
        new=[key for key in low if low[key] and not previous.get(key+"_alerted")]
        values={"ton":str(ton),"stars":str(stars),"checked_at":time.time(),
                **{key+"_alerted":bool(low[key] and (enabled or previous.get(key+"_alerted"))) for key in low}}
        if row:row.data=values;row.updated=time.time()
        else:session.add(Entity(kind="balance_watch",id="mrkt",data=values))
        if enabled and new:
            text="⚠️ Низкий баланс для выдачи подарков\n\n"
            text+="MRKT: "+str(ton)+" TON"+(" — меньше 15 TON" if low["ton"] else "")
            text+="\nStars: "+str(stars)+(" — осталось 50 или меньше" if low["stars"] else "")
            text+="\n\nПополните баланс, чтобы выдача заказов не остановилась."
            session.add(Entity(kind="notification",id="balance-low:"+uid(),data={"state":"PENDING","event":"low_balance","payload":{"text":text}}))
    return {"mrkt_ton":str(ton),"stars":str(stars)}


def wait_for_balance(db,op,resource="ton"):
    oid=op["order_id"]
    order=db.get("order",oid)
    if not order:raise ValueError("Заказ не найден")
    if order.get("stage")=="WAITING_BALANCE":return
    token=uid()
    settings=db.settings()
    with db.tx() as session:
        operation=session.get(Entity,("operation",op["id"]))
        row=session.get(Entity,("order",oid))
        if not operation or operation.data.get("submitted_at") or operation.data.get("state")!="QUEUED":
            raise ValueError("Результат закупки требует сверки")
        if not row or row.data.get("manual_hold") or row.data.get("cancel_requested") or row.data.get("stage")!="PURCHASING":
            raise ValueError("Обработка заказа остановлена")
        cid=row.data.get("catalog_credit_id")
        credit=session.get(Entity,("catalog_credit_payment",cid)) if cid else None
        if cid and (not credit or credit.data.get("fulfillment_order_id")!=oid or credit.data.get("state")!="EXECUTING" or credit.data.get("revision")!=row.data.get("catalog_revision")):
            raise ValueError("Выбор подарка изменился")
        operation.data={**operation.data,"state":"FAILED","error":"Недостаточно Stars" if resource=="stars" else "Недостаточно TON на MRKT","balance_wait":True}
        operation.updated=time.time()
        row.data={**row.data,"stage":"WAITING_BALANCE","reason":ACCEPTED,"balance_wait_token":token,"activity_at":time.time()}
        row.updated=time.time()
        if credit:
            credit.data={**credit.data,"state":"EXECUTING","notice":None}
            credit.updated=time.time()
        key="owner-balance:"+token
        session.add(Entity(kind="balance_retry_button",id=token,data={"order_id":oid,"state":"READY","notification_id":key}))
        if settings.get("notifications_enabled"):
            text="🚨 <b>Не хватает Stars для выдачи NFT</b>\n\n" if resource=="stars" else "🚨 <b>Не хватает TON на покупку NFT</b>\n\n"
            text+="🎁 "+escape(order.get("title") or order.get("gift_slug") or oid)
            needed=settings.get("transfer_stars_reserve",25) if resource=="stars" else (order.get("snapshot") or {}).get("quote_ton")
            if needed is not None:text+="\nНужно: <b>"+escape(str(needed))+(" Stars" if resource=="stars" else " TON")+"</b>"
            text+="\n\nКлиент ждёт подарок. Срочно пополните "+("Stars" if resource=="stars" else "MRKT")+", затем нажмите «Повторить заказ»."
            session.add(Entity(kind="notification",id=key,data={"state":"PENDING","event":"balance_wait","order_id":oid,
                "payload":{"text":text,"parse_mode":"HTML","reply_markup":{"inline_keyboard":[[{"text":"🔄 Повторить заказ","callback_data":"mb:"+token}]]}}}))


def retry(db,token):
    """One atomic retry; all payment, quote, recipient and safety checks run again."""
    from .operations import order_retry_reason
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        table=Entity.__table__
        def read(kind,key):return conn.execute(select(table.c.data).where(table.c.kind==kind,table.c.id==key)).scalar_one_or_none()
        record=read("balance_retry_button",token)
        if not record or record.get("state")!="READY":raise ValueError("Повтор уже запрошен")
        oid=record["order_id"];order=read("order",oid)
        if not order or order.get("stage")!="WAITING_BALANCE" or order.get("balance_wait_token")!=token or order.get("manual_hold"):
            raise ValueError("Заказ изменился. Проверьте его в панели")
        operations=conn.execute(select(table.c.id,table.c.data).where(table.c.kind=="operation")).all()
        ops=[(key,value) for key,value in operations if value.get("order_id")==oid]
        if any(value.get("submitted_at") or value.get("state")=="VERIFYING" for _,value in ops):
            raise ValueError("Результат предыдущей операции требует сверки")
        spent=any(v.get("order_id")==oid for v in conn.execute(select(table.c.data).where(table.c.kind=="ledger")).scalars())
        reason=order_retry_reason({**order,"stage":"MANUAL"},[v for _,v in ops],spent)
        if reason:raise ValueError(reason)
        for payload in conn.execute(select(Task.payload).where(Task.state=="RUNNING",Task.kind.in_(["process_order","execute"])) ).scalars():
            if payload.get("order_id")==oid or payload.get("operation_id") in {key for key,_ in ops}:raise ValueError("Заказ уже обрабатывается")
        cid=order.get("catalog_credit_id")
        if cid:
            credit=read("catalog_credit_payment",cid) or {}
            if credit.get("state")!="EXECUTING" or credit.get("fulfillment_order_id")!=oid or credit.get("revision")!=order.get("catalog_revision"):
                raise ValueError("Выбор подарка изменился. Проверьте заказ в панели")
        def write(kind,key,value):conn.execute(table.update().where(table.c.kind==kind,table.c.id==key).values(data=value,updated=time.time()))
        for key,value in ops:
            if value.get("state")=="FAILED" and not value.get("submitted_at"):
                write("operation",key,{**value,"state":"RETRY_PENDING","error":None,"rate_limit_retries":0,"mrkt_retry_deadline":None})
        write("order",oid,{**order,"stage":"DISCOVERED","reason":None,"mrkt_retry_deadline":None,"allow_low_profit":False,"activity_at":time.time()})
        write("balance_retry_button",token,{**record,"state":"USED"})
        dedupe="order:"+oid
        if not conn.execute(select(Task.id).where(Task.dedupe==dedupe)).first():
            conn.execute(Task.__table__.insert().values(id=uid(),kind="process_order",payload={"order_id":oid},dedupe=dedupe,state="READY",attempts=0,due=time.time()))
        conn.commit()


def handle(service,bot,callback):
    db=service.db;message=callback.get("message") or {};chat=message.get("chat") or {}
    owner=db.settings().get("owner_chat_id")
    def answer(text):return bot.request("answerCallbackQuery",{"callback_query_id":callback["id"],"text":text,"show_alert":True})
    if not owner or callback.get("from",{}).get("id")!=owner or chat.get("id")!=owner or chat.get("type")!="private":
        answer("Доступно только владельцу магазина");return
    token=str(callback.get("data","")).removeprefix("mb:")
    record=db.get("balance_retry_button",token) or {}
    notice=db.get("notification",record.get("notification_id")) or {}
    if not message.get("message_id") or notice.get("message_id")!=message["message_id"]:
        answer("Сообщение заказа не подтверждено");return
    try:retry(db,token)
    except ValueError as exc:answer(str(exc));return
    answer("Повтор запрошен. Проверим оплату, наличие подарка и баланс заново.")
    bot.request("editMessageReplyMarkup",{"chat_id":owner,"message_id":message["message_id"],"reply_markup":{"inline_keyboard":[]}})
