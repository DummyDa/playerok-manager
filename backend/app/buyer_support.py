"""Buyer support requests in known deal chats; no automatic repeated replies."""
from datetime import datetime
from html import escape
import time

from .safety import gate


def poll(service):
    from .integrations.clients import Playerok
    db=service.db
    gate(db)
    clock=db.get("support_cursor","main")
    if not clock:
        db.put("support_cursor","main",{"started_at":time.time(),"offset":0})
        return
    if (db.get("throttle","playerok") or {}).get("next",0)>time.time():return
    sources=db.list("order",100000)+db.list("catalog_credit_receipt",100000)
    chats={}
    for source in sources:
        if source.get("chat_id") and source.get("buyer_id"):
            chats.setdefault(source["chat_id"],{})[source["buyer_id"]]=source["id"]
    keys=sorted(chats)
    if not keys:return
    client=Playerok(db);account=client.account()
    offset=clock.get("offset",0)%len(keys)
    for step in range(min(3,len(keys))):
        chat=keys[(offset+step)%len(keys)]
        for message in reversed(account.get_chat_messages(chat,count=24).messages):
            if str(getattr(message,"text","") or "").strip().casefold()!="!продавец" or getattr(message,"deleted_at",None):continue
            buyer=str(getattr(getattr(message,"user",None),"id",""))
            if buyer not in chats[chat]:continue
            try:created=datetime.fromisoformat(str(message.created_at).replace("Z","+00:00")).timestamp()
            except (ValueError,TypeError):continue
            if created<clock["started_at"]:continue
            key=chat+":"+str(message.id)
            if db.get("support_request",key):continue
            # One notification per chat per five minutes, including repeated commands.
            recent=db.get("support_chat",chat) or {}
            if recent.get("last_at",0)>time.time()-300:
                db.put("support_request",key,{"state":"COALESCED","chat_id":chat});continue
            deal=chats[chat][buyer]
            db.put("support_request",key,{"state":"SUBMITTED","chat_id":chat,"buyer_id":buyer,"deal_id":deal})
            db.put("support_chat",chat,{"last_at":time.time()})
            db.log("buyer_support","Покупатель вызвал продавца","Команда !продавец",order_id=deal)
            db.put("notification","support:"+key,{"event":"support","state":"PENDING","order_id":deal,"payload":{"text":"💬 Покупатель просит помощи\nЗаказ: "+escape(deal),"parse_mode":"HTML","reply_markup":{"inline_keyboard":[[{"text":"Открыть заказ","url":"https://playerok.com/deal/"+deal}]]}}})
            try:
                result=client.send_chat_message(chat,"💬 Обращение сохранено для продавца. Напишите свой вопрос в этом чате — продавец ответит здесь.")
                db.put("support_request",key,{"state":"SENT" if result and result.get("id") else "UNKNOWN"})
            except Exception:
                db.put("support_request",key,{"state":"UNKNOWN"})
    db.put("support_cursor","main",{"offset":(offset+3)%len(keys)})
