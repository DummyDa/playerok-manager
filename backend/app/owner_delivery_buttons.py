"""Owner-only Telegram confirmation of manual delivery."""
import time
from .db import uid,Entity,Task
from sqlalchemy import select,update
from .manual_delivery import allowed


def review_selection(db,order):
    if not order or order.get("stage")!="MANUAL" or order.get("cancel_requested") or order.get("manual_delivery_receipt"):return None
    from .manual_fulfillment import target
    try:current,wallet=target(db,order["id"])
    except ValueError:return None
    if current["id"]!=order["id"] or wallet.get("state") not in {"REVIEW","EXECUTING"}:return None
    if wallet.get("fulfillment_order_id")!=order["id"] or wallet.get("revision")!=order.get("catalog_revision"):return None
    slug=order.get("gift_slug") or wallet["selected"].get("slug")
    recipient=order.get("recipient") or wallet.get("recipient")
    if not slug or not recipient or slug!=wallet["selected"].get("slug"):return None
    return {"slug":slug,"recipient":recipient,"revision":wallet.get("revision"),"mode":"review"}


def eligible(db,order):return allowed(db,order) or bool(review_selection(db,order))


def selection(db,order):
    return review_selection(db,order) or {"slug":order.get("gift_slug"),"recipient":order.get("recipient"),"recipient_id":order.get("recipient_id"),"mode":"normal"}


def needs_cost(db,order):
    return order.get("owner_purchase_rub") is None and not any(e.get("order_id")==order["id"] and e.get("type")=="purchase" for e in db.list("ledger",100000))


def queue_review(db,token,record,cost=None):
    from .manual_fulfillment import prepare
    oid=prepare(db,record["order_id"],cost)
    task=db.enqueue("manual_review_complete",{"order_id":oid},key="manual-review:"+oid)
    db.put("owner_delivery_button",token,{"state":"PENDING","task_id":task})


def button(db,order):
    token=uid()
    selected=selection(db,order)
    db.put("owner_delivery_button",token,{"state":"READY","order_id":order["id"],"gift":{"slug":selected["slug"],"recipient":selected["recipient"],"recipient_id":order.get("recipient_id")}})
    return {"text":"✅ Выдан","callback_data":"md:"+token+":ask"}


def handle(service,bot,callback):
    db=service.db
    message=callback.get("message") or {};chat=message.get("chat") or {}
    owner=db.settings().get("owner_chat_id")
    def answer(text,alert=True):return bot.request("answerCallbackQuery",{"callback_query_id":callback["id"],"text":text,"show_alert":alert})
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
    if not eligible(db,order):answer("Заказ ещё не готов или требует сверки. Проверьте его в панели.");return
    selected=selection(db,order)
    expected=record.get("gift")
    if expected and expected!={"slug":selected["slug"],"recipient":selected["recipient"],"recipient_id":order.get("recipient_id")}:
        answer("Подарок или получатель изменились. Откройте актуальное сообщение заказа.");return
    base={"chat_id":owner,"message_id":message["message_id"]}
    keyboard=(notice.get("payload") or {}).get("reply_markup",{}).get("inline_keyboard",[])
    links=[[item for item in row if item.get("url")] for row in keyboard]
    links=[row for row in links if row]
    if action=="no":
        db.put("owner_delivery_button",token,{"state":"READY"})
        bot.request("editMessageText",{**base,"text":notice["payload"]["text"],"parse_mode":"HTML","reply_markup":{"inline_keyboard":[*links,[{"text":"✅ Выдан","callback_data":"md:"+token+":ask"}]]}})
        answer("Подтверждение отменено");return
    if action=="yes":
        # Previously displayed confirmation buttons remain usable only while
        # their exact gift/recipient confirmation is still current.
        if record["state"]!="ASKING" or time.time()-record.get("asked_at",0)>600 or record.get("selection")!=selected:
            answer("Подтверждение устарело. Нажмите «Выдан» заново.");return
    elif action!="ask":
        answer("Неизвестная кнопка");return
    if record["state"]=="ASKING_COST" and time.time()-record.get("asked_at",0)<=600 and record.get("selection")==selected:
        answer("Укажите фактическую закупку ответом на сообщение бота",False);return
    if selected["mode"]=="review":
        if needs_cost(db,order):
            result=bot.request("sendMessage",{"chat_id":owner,"text":"Подарок "+selected["slug"]+" передан @"+selected["recipient"].lstrip("@")+". Система не зафиксировала закупку. Ответьте на это сообщение фактической суммой закупки в рублях (например, 508,57), чтобы завершить заказ и посчитать прибыль. Для отмены ответьте «Отмена».","reply_markup":{"force_reply":True,"selective":True}})
            db.put("owner_delivery_button",token,{"state":"ASKING_COST","cost_message_id":result["message_id"],"asked_at":time.time(),"selection":selected})
            answer("Укажите фактическую закупку ответом на сообщение бота",False);return
        try:queue_review(db,token,record)
        except ValueError as exc:answer(str(exc));return
        answer("Принято. Подтверждаем ручную выдачу и связанные сделки на Playerok.",False)
        bot.request("editMessageReplyMarkup",{**base,"reply_markup":{"inline_keyboard":links}})
        return
    queued=False
    with db.tx() as session:
        row=session.get(Entity,("owner_delivery_button",token))
        if row and row.data.get("state") in {"READY","ASKING","ASKING_COST"}:
            queued=bool(session.execute(update(Entity).where(Entity.kind=="owner_delivery_button",Entity.id==token,Entity.data==row.data).values(data={**row.data,"state":"PENDING"},updated=time.time())).rowcount)
            key="manual-delivered:"+oid
            if queued and not session.scalar(select(Task.id).where(Task.dedupe==key)):
                session.add(Task(kind="manual_delivery_confirm",payload={"order_id":oid},dedupe=key,due=time.time()))
    if not queued:
        answer("Подтверждение уже обрабатывается");return
    answer("Принято. Отмечаем выдачу и связанные сделки на Playerok.",False)
    bot.request("editMessageReplyMarkup",{**base,"reply_markup":{"inline_keyboard":links}})


def handle_reply(service,bot,message):
    import re
    db=service.db;owner=db.settings().get("owner_chat_id");chat=message.get("chat") or {}
    if not owner or message.get("from",{}).get("id")!=owner or chat.get("id")!=owner or chat.get("type")!="private":return False
    reply=(message.get("reply_to_message") or {}).get("message_id")
    if not reply:return False
    record=next((r for r in db.list("owner_delivery_button",100000) if r.get("state")=="ASKING_COST" and r.get("cost_message_id")==reply),None)
    if not record:return False
    def say(text):bot.request("sendMessage",{"chat_id":owner,"text":text})
    order=db.get("order",record["order_id"]) or {}
    if time.time()-record.get("asked_at",0)>600 or record.get("selection")!=selection(db,order) or not review_selection(db,order):
        say("Подтверждение устарело. Нажмите «Выдан» заново.");return True
    text=str(message.get("text","")).strip()
    if text.casefold()=="отмена":
        db.put("owner_delivery_button",record["id"],{"state":"READY"});say("Подтверждение отменено.");return True
    from .pricing import D
    if not re.fullmatch(r"\d{1,8}(?:[.,]\d{1,2})?",text) or not 0<D(text.replace(",","."))<=10000000:
        say("Укажите положительную сумму до 10 000 000 ₽, например 508,57.");return True
    try:queue_review(db,record["id"],record,text.replace(",","."))
    except ValueError as exc:say(str(exc));return True
    say("Принято. Подтверждаем ручную выдачу и связанные сделки на Playerok.")
    return True
