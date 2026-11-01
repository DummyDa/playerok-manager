"""Owner-only Telegram confirmation of manual delivery."""
import time
from html import escape
from .db import uid,Entity,Task
from sqlalchemy import select,update
from .manual_delivery import allowed


def button(db,order):
    token=uid()
    db.put("owner_delivery_button",token,{"state":"READY","order_id":order["id"]})
    return {"text":"✅ Выдан","callback_data":"md:"+token+":ask"}


def handle(service,bot,callback):
    db=service.db
    message=callback.get("message") or {};chat=message.get("chat") or {}
    owner=db.settings().get("owner_chat_id")
    def answer(text):return bot.request("answerCallbackQuery",{"callback_query_id":callback["id"],"text":text,"show_alert":True})
    if not owner or callback.get("from",{}).get("id")!=owner or chat.get("id")!=owner or chat.get("type")!="private":
        answer("Доступно только владельцу магазина");return
    parts=str(callback.get("data","")).split(":")
    if len(parts)!=3 or parts[0]!="md":answer("Неизвестная кнопка");return
    _,token,action=parts
    record=db.get("owner_delivery_button",token)
    if not record:answer("Кнопка недействительна");return
    oid=record["order_id"];order=db.get("order",oid) or {}
    notice=db.get("notification","owner:"+oid+":purchased") or {}
    if notice.get("message_id")!=message.get("message_id"):
        answer("Сообщение заказа не подтверждено");return
    if order.get("manual_delivery_receipt") or order.get("stage")=="COMPLETE" or record["state"]=="PENDING":
        answer("Выдача уже подтверждена или обрабатывается");return
    if not allowed(db,order):answer("Заказ ещё не готов или требует сверки. Проверьте его в панели.");return
    base={"chat_id":owner,"message_id":message["message_id"]}
    keyboard=(notice.get("payload") or {}).get("reply_markup",{}).get("inline_keyboard",[])
    links=[[item for item in row if item.get("url")] for row in keyboard]
    links=[row for row in links if row]
    if action=="ask":
        db.put("owner_delivery_button",token,{"state":"ASKING","asked_at":time.time(),"slug":order["gift_slug"],"recipient_id":order["recipient_id"]})
        body=notice["payload"]["text"]+"\n\n<b>Подарок уже передан @"+escape(order["recipient"].lstrip("@"))+"?</b>"
        bot.request("editMessageText",{**base,"text":body,"parse_mode":"HTML","reply_markup":{"inline_keyboard":[*links,[{"text":"Да, подарок передан","callback_data":"md:"+token+":yes"}],[{"text":"Назад","callback_data":"md:"+token+":no"}]]}})
        answer("Подтвердите только после передачи подарка получателю");return
    if action=="no":
        db.put("owner_delivery_button",token,{"state":"READY"})
        bot.request("editMessageText",{**base,"text":notice["payload"]["text"],"parse_mode":"HTML","reply_markup":{"inline_keyboard":[*links,[{"text":"✅ Выдан","callback_data":"md:"+token+":ask"}]]}})
        answer("Подтверждение отменено");return
    if action!="yes" or record["state"]!="ASKING" or time.time()-record.get("asked_at",0)>600 or record.get("slug")!=order.get("gift_slug") or record.get("recipient_id")!=order.get("recipient_id"):
        answer("Подтверждение устарело. Нажмите «Выдан» заново.");return
    queued=False
    with db.tx() as session:
        row=session.get(Entity,("owner_delivery_button",token))
        if row and row.data.get("state")=="ASKING":
            queued=bool(session.execute(update(Entity).where(Entity.kind=="owner_delivery_button",Entity.id==token,Entity.data==row.data).values(data={**row.data,"state":"PENDING"},updated=time.time())).rowcount)
            key="manual-delivered:"+oid
            if queued and not session.scalar(select(Task.id).where(Task.dedupe==key)):
                session.add(Task(kind="manual_delivery_confirm",payload={"order_id":oid},dedupe=key,due=time.time()))
    if not queued:
        answer("Подтверждение уже обрабатывается");return
    answer("Принято. Отмечаем выдачу и связанные сделки на Playerok.")
    bot.request("editMessageReplyMarkup",{**base,"reply_markup":{"inline_keyboard":links}})
