"""Mandatory catalog instructions, encrypted outbox, single-attempt delivery."""
from string import Formatter
import time
import re
from urllib.parse import urlsplit

from .db import Entity
from .funded_orders import cas
from .safety import gate
from .catalog_texts import PROMO,TOPUP,SUPPORT,CONFIRM

FIELDS={"amount","recipient","url","code","balance","gift","gift_link","topup","payment_url","delivery_notice"}


def validate_template(template):
    for _,field,spec,conversion in Formatter().parse(template):
        if field is not None and (field not in FIELDS or spec or conversion):raise ValueError("Подстановки сообщений каталога: "+", ".join("{"+f+"}" for f in sorted(FIELDS)))
    return template


def validate_url(value):
    value=value.strip().rstrip("/")
    if not value:return None
    parsed=urlsplit(value)
    local=parsed.scheme=="http" and parsed.hostname in {"localhost","127.0.0.1","::1"}
    if (parsed.scheme!="https" and not local) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in value):raise ValueError("Укажите HTTPS-адрес каталога или локальный http://127.0.0.1:3180")
    parsed.port
    return value


def catalog_base(db):return validate_url(db.settings().get("catalog_public_url",""))


def rendered(template,default,fields):
    try:
        text=validate_template(template or default).format_map(fields)
        if not text.strip() or len(text)>4000:raise ValueError()
        return text
    except ValueError:return default.format_map(fields)


def values(db,credit,amount="0"):
    from .catalog_credit import catalog_link,balance
    code=db.cipher.decrypt(credit["code_encrypted"].encode()).decode()
    code=code[:4]+"-"+code[4:]
    base=catalog_base(db)
    return {"amount":str(amount),"recipient":"@"+credit["recipient"].lstrip("@") if credit.get("recipient") else "укажите в каталоге","url":catalog_link(credit,code,base) if base else "","code":code,"balance":str(balance(credit)),"gift":(credit.get("selected") or {}).get("slug",""),"gift_link":credit.get("gift_link") or "","topup":credit.get("topup_rub","0"),"payment_url":"","delivery_notice":"🎁 Подарок передаст продавец. Если есть вопросы, напишите !продавец." if db.settings().get("manual_gift_delivery") else ""}


def queue(db,credit,event,source,text,catalog_url=None):
    from .manual_delivery import buyer_copy
    text=buyer_copy(db,text)
    if db.settings().get("manual_gift_delivery") and not event.startswith(("promo_paid","topup_paid","delivered:","selected:")) and "Подарок передаст продавец" not in text:
        text+="\n\n🎁 Подарок передаст продавец. Если есть вопросы, напишите !продавец."
    if not source.get("chat_id") or not source.get("buyer_id"):return
    key=credit["id"]+":"+event+":"+source["chat_id"]
    if db.get("catalog_message",key):return
    if "!продавец" not in text:text+="\n\n"+SUPPORT
    if catalog_url:
        text=text.replace("Если подарок дешевле — остаток сохранится на балансе; если дороже — потребуется только доплата.","Одна оплата — один подарок. После выдачи остаток списывается и код закрывается. Допуск без доплаты — 20 ₽; минимальная доплата — 90 ₽.")
        text=text.replace(catalog_url,"").strip()
        instruction="Ссылка в каталог:"
        lines=[]
        for line in text.splitlines():
            if re.search(r"выберите .*подарок в каталоге",line,re.I):instruction=line.strip()
            elif line.strip()!="Ссылка в каталог:":lines.append(line)
        text="\n".join(lines).strip()
        if "VPN" not in text:text+="\n\n🖼 Если картинки не загружаются, попробуйте включить VPN."
        text+="\n\n"+instruction
    if not text.strip() or len(text)>4000:raise ValueError("Сообщение каталога должно содержать 1–4000 символов")
    data={"state":"QUEUED","credit_id":credit["id"],"event":event,"deal_id":source["id"],"chat_id":source["chat_id"],"buyer_id":source["buyer_id"],"encrypted_text":db.cipher.encrypt(text.encode()).decode()}
    with db.tx() as session:
        session.add(Entity(kind="catalog_message",id=key,data=data))
        if catalog_url:
            session.add(Entity(kind="catalog_message",id=key+":catalog_link",data={**data,"event":event+":catalog_link","after_message_id":key,"encrypted_text":db.cipher.encrypt(catalog_url.encode()).decode()}))
    db.enqueue("catalog_message",{"message_id":key},key="catalog_message:"+key)


def source_rows(db,cid):
    return [p for p in db.list("catalog_credit_receipt",100000) if p.get("credit_id")==cid and p.get("accepted")]


def promo_paid(db,credit,source):
    if credit["state"] in {"EXHAUSTED","DELETED"}:return
    fields=values(db,credit,source["amount_rub"])
    if not fields["url"]:
        db.put("catalog_credit_payment",credit["id"],{"notice":"Укажите адрес каталога: покупателю нужно отправить ссылку"})
        text="✅ Оплата получена: "+fields["amount"]+" ₽\nКод заказа: "+fields["code"]+"\nПродавец готовит ссылку на каталог. Повторно оплачивать заказ не нужно."
        queue(db,credit,"promo_waiting_url",source,text);return
    template=db.settings().get("catalog_promo_paid_template") or PROMO
    queue(db,credit,"promo_paid" if source["id"]==credit.get("root_order_id") else "promo_paid:"+source["id"],source,rendered(template,PROMO,fields),catalog_url=fields["url"])
    if credit.get("notice","") and credit["notice"].startswith("Укажите адрес каталога"):
        db.put("catalog_credit_payment",credit["id"],{"notice":None})


def topup_requested(db,credit,checkout):
    if not checkout.get("url"):return
    text="💳 Доплата за выбранный подарок: "+str(checkout["topup_rub"])+" ₽\nПолучатель: @"+credit["recipient"].lstrip("@")+"\n\nОплатите на Playerok:\n"+checkout["url"]+"\n\nЛот действует 15 минут. После подтверждения оплаты начнётся выдача подарка."
    for source in source_rows(db,credit["id"]):
        if source.get("kind")=="ORIGINAL":queue(db,credit,"topup_requested:"+str(credit["revision"]),source,text)


def selection_accepted(db,credit):
    if credit.get("state")!="EXECUTING" or not credit.get("selected"):return
    from .catalog_texts import SELECTED
    fields=values(db,credit)
    fields["delivery_notice"]=fields["delivery_notice"] or "Подарок готовится к автоматической выдаче."
    text=rendered(db.settings().get("catalog_selected_template"),SELECTED,fields)
    for source in source_rows(db,credit["id"]):
        if source.get("kind")=="ORIGINAL" or source.get("revision")==credit["revision"]:
            queue(db,credit,"selected:"+str(credit["revision"]),source,text)


def topup_paid(db,credit,source):
    fields=values(db,credit,source["amount_rub"])
    if not source.get("accepted"):
        text="Оплата получена, но её нужно проверить вручную. Пожалуйста, напишите продавцу в этом чате. Повторно оплачивать лот не нужно."
    elif credit["state"]=="EXECUTING":
        text=rendered(db.settings().get("catalog_topup_paid_template"),TOPUP,fields)
    elif credit["state"]=="AWAITING_SELECTION":
        text="✅ Доплата зачислена: "+fields["amount"]+" ₽\nПожалуйста, подтвердите выбор подарка заново в каталоге.\n"+fields["url"]
    else:text="✅ Оплата получена: "+fields["amount"]+" ₽\nЗаказ требует проверки продавцом. Повторно оплачивать лот не нужно."
    queue(db,credit,"topup_paid:"+source["id"],source,text,catalog_url=fields["url"] if fields["url"] and fields["url"] in text else None)


def choice_unavailable(db,credit,order):
    fields=values(db,credit)
    text="🎁 Выбранный подарок сейчас нельзя выдать. Оплата сохранена на балансе заказа.\n\n"
    text+="🔄 Выберите другой подарок в каталоге:\n"+fields["url"] if fields["url"] else "🔄 Для выбора другого подарка запросите ссылку у продавца."
    text+="\n\n↩️ Хотите вернуть деньги? Напишите !продавец в этом чате и укажите, что нужен возврат. Продавец проверит заказ и оформит возврат через Playerok."
    for source in source_rows(db,credit["id"]):
        if source.get("kind")=="ORIGINAL" or source.get("revision")==order["catalog_revision"]:
            queue(db,credit,"unavailable:"+str(order["catalog_revision"]),source,text,catalog_url=fields["url"] or None)


def gift_delivered(db,credit,order):
    from .buyer_messages import render_template
    from .catalog_texts import DELIVERED
    text=render_template(db.settings().get("buyer_delivered_template") or DELIVERED,{**order,"id":credit.get("root_order_id") or order.get("id") or credit["id"]})
    for source in source_rows(db,credit["id"]):
        if source.get("kind")=="ORIGINAL" or source.get("revision")==order["catalog_revision"]:queue(db,credit,"delivered:"+str(order["catalog_revision"]),source,text)


def send(service,key):
    from .integrations.clients import Playerok
    db=service.db;message=db.get("catalog_message",key)
    if not message or message["state"]!="QUEUED":return
    if message.get("after_message_id"):
        parent=(db.get("catalog_message",message["after_message_id"]) or {}).get("state")
        if parent=="SKIPPED":db.put("catalog_message",key,{"state":"SKIPPED"});return
        if parent!="SENT":return
    credit=db.get("catalog_credit_payment",message["credit_id"]) or {}
    stale=(message["event"].startswith("promo_") and credit.get("state")=="DELETED")
    if message["event"].startswith("selected:"):
        stale=credit.get("state")!="EXECUTING" or str(credit.get("revision"))!=message["event"].split(":")[-1]
    if message["event"].startswith("topup_requested:"):
        stale=credit.get("state")!="AWAITING_TOPUP" or str(credit.get("revision"))!=message["event"].split(":")[-1]
        checkout=db.get("catalog_checkout",credit.get("checkout_id")) or {}
        stale=stale or checkout.get("state")!="READY" or checkout.get("expires_at",0)<=time.time()
    if stale:
        db.put("catalog_message",key,{"state":"SKIPPED"})
        if db.get("catalog_message",key+":catalog_link"):db.put("catalog_message",key+":catalog_link",{"state":"SKIPPED"})
        return
    gate(db)
    source=Playerok(db).orders(deal_id=message["deal_id"])[0]
    if source.get("buyer_id")!=message["buyer_id"] or source.get("chat_id")!=message["chat_id"]:raise ValueError("Чат и покупатель сообщения не подтверждены")
    if source["status"] not in {"PAID","SENT","CONFIRMED","CONFIRMED_AUTOMATICALLY"}:
        db.put("catalog_message",key,{"state":"SKIPPED"});return
    if not cas(db,"catalog_message",key,{"QUEUED"},{"state":"SUBMITTED","submitted_at":time.time()}):return
    try:
        result=Playerok(db).send_chat_message(message["chat_id"],db.cipher.decrypt(message["encrypted_text"].encode()).decode())
        if not result.get("id"):raise ValueError("Не подтверждена отправка сообщения")
        db.put("catalog_message",key,{"state":"SENT","remote_id":result["id"]})
        from .catalog_credit import audit
        audit(db,credit,"MESSAGE_SENT",deal_id=message["deal_id"],event=message["event"])
        if db.get("catalog_message",key+":catalog_link"):
            try:send(service,key+":catalog_link")
            except Exception as exc:db.log("catalog_link_pending","Ссылка ожидает отправки",db.redact(exc),"ERROR")
    except Exception:
        db.put("catalog_message",key,{"state":"UNKNOWN","error":"Отправка сообщения не подтверждена. Повтор только после сверки чата"})
        from .catalog_credit import audit
        audit(db,credit,"MESSAGE_UNKNOWN",deal_id=message["deal_id"])
        if not credit.get("notice"):db.put("catalog_credit_payment",credit["id"],{"notice":"Отправка сообщения покупателю не подтверждена. Проверьте чат Playerok"})


def tick(db):
    for message in db.list("catalog_message",100000):
        if message["state"]=="QUEUED":db.enqueue("catalog_message",{"message_id":message["id"]},key="catalog_message:"+message["id"],due=max(time.time(),(db.get("throttle","playerok") or {}).get("next",0)))
    if catalog_base(db):
        for source in db.list("catalog_credit_receipt",100000):
            if source.get("kind")!="ORIGINAL" or not source.get("accepted"):continue
            credit=db.get("catalog_credit_payment",source["credit_id"])
            if credit and credit["state"]!="DELETED":promo_paid(db,credit,source)
