"""Buyer replies in Playerok deal chats, with a persistent at-most-once outbox."""
import time
from string import Formatter

from sqlalchemy import update

from .db import Entity
from .safety import IntegrationError, gate

FIELDS={"order_id","recipient","title","price","gift","gift_link"}


def validate_template(text):
    for _,field,spec,conversion in Formatter().parse(text):
        if field is not None and (field not in FIELDS or spec or conversion):
            raise ValueError("Допустимые подстановки: " + ", ".join("{"+key+"}" for key in sorted(FIELDS)))
    return text


def render_template(text, order):
    validate_template(text)
    slug=order.get("gift_slug") or ""
    recipient=order.get("recipient") or ""
    result=text.format_map({"order_id":order["id"],"recipient":"@"+recipient.lstrip("@") if recipient else "",
        "title":order.get("title") or "","price":str(order.get("sale_rub") or ""),
        "gift":slug,"gift_link":"https://t.me/nft/"+slug if slug else ""}).strip()
    if len(result)>4000:raise ValueError("Сообщение после подстановок превышает 4000 символов")
    return result


def queue(db, order, event):
    if event not in {"paid","delivered"}:raise ValueError("Неизвестное событие сообщения")
    key=order["id"]+":"+event
    if db.get("buyer_message",key):return
    settings=db.settings()
    if not settings.get("buyer_"+event+"_enabled"):return
    try:
        text=render_template(settings.get("buyer_"+event+"_template") or "",order)
    except ValueError as exc:
        db.put("buyer_message",key,{"order_id":order["id"],"event":event,"state":"FAILED","error":str(exc)})
        db.log("buyer_message_error","Не удалось подготовить сообщение покупателю",str(exc),"ERROR",order_id=order["id"])
        return
    if not text:return
    db.put("buyer_message",key,{"order_id":order["id"],"event":event,"state":"QUEUED","text":text,
                               "chat_id":order.get("chat_id")})
    db.enqueue("buyer_message",{"message_id":key},key="buyer_message:"+key)


def send(service, message_id):
    from .integrations.clients import Playerok
    db=service.db
    message=db.get("buyer_message",message_id)
    if not message or message["state"]!="QUEUED":return
    submitted=False
    try:
        if not db.settings().get("buyer_"+message["event"]+"_enabled"):
            db.put("buyer_message",message_id,{"state":"SKIPPED","reason":"Шаблон отключён"})
            return
        gate(db)
        playerok=Playerok(db)
        fresh=playerok.orders(deal_id=message["order_id"])
        if not fresh or fresh[0]["status"] not in {"PAID","SENT","CONFIRMED"}:
            db.put("buyer_message",message_id,{"state":"SKIPPED","reason":"Оплата заказа не подтверждена"})
            return
        chat_id=fresh[0].get("chat_id")
        if not chat_id or (message.get("chat_id") and message["chat_id"]!=chat_id):
            raise IntegrationError("Не подтверждён чат покупателя для сообщения")
        order=db.get("order",message["order_id"])
        if message["event"]=="delivered" and (not order or not service.delivery_confirmed(order)):
            raise IntegrationError("Передача NFT не подтверждена; сообщение не отправлено")
        if not db.settings().get("buyer_"+message["event"]+"_enabled"):
            db.put("buyer_message",message_id,{"state":"SKIPPED","reason":"Шаблон отключён"})
            return
        gate(db)
        with db.tx() as session:
            row=session.get(Entity,("buyer_message",message_id))
            if not row or row.data.get("state")!="QUEUED":return
            data={**row.data,"state":"SUBMITTED","submitted_at":time.time(),"chat_id":chat_id}
            claimed=session.execute(update(Entity).where(Entity.kind=="buyer_message",Entity.id==message_id,
                Entity.data==row.data).values(data=data,updated=time.time())).rowcount
        if not claimed:return
        submitted=True
        result=playerok.send_chat_message(chat_id,message["text"])
        if not isinstance(result,dict) or not result.get("id"):
            raise IntegrationError("Playerok не подтвердил ID сообщения")
        db.put("buyer_message",message_id,{"state":"SENT","remote_message_id":str(result["id"]),"error":None})
        db.log("buyer_message_sent","Сообщение покупателю отправлено",message["event"],order_id=message["order_id"])
    except Exception as exc:
        db.put("buyer_message",message_id,{"state":"UNKNOWN" if submitted else "FAILED","error":db.redact(exc)})
        db.log("buyer_message_error","Сообщение покупателю требует проверки",db.redact(exc),"ERROR",order_id=message["order_id"])
        raise
