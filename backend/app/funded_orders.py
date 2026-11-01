"""One paid entitlement, many Playerok payments, exactly one NFT delivery."""
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode, urlsplit

from sqlalchemy import update
from .db import Entity, uid
from .pricing import D, net_coefficients
from .safety import ManualRequired, gate

ALPHABET="ABCDEFGHJKMNPQRSTUVWXYZ"
OPEN={"AWAITING_SELECTION","PREPARING","AWAITING_TOPUP","EXECUTING","CHOICE_REQUIRED","REVIEW"}
PAID={"PAID","SENT","CONFIRMED","CONFIRMED_AUTOMATICALLY"}
DEFAULT_MESSAGE="Оплата получена: {amount} ₽\nПолучатель: {recipient}\nВыберите подарок в каталоге: {url}\nВаш код: {code}\nСсылка и код действуют до получения подарка. Повторно оплачивать полную стоимость не нужно."


def cas(db,kind,key,states,values):
    with db.tx() as session:
        row=session.get(Entity,(kind,key))
        if not row or row.data.get("state") not in states:return False
        return bool(session.execute(update(Entity).where(Entity.kind==kind,Entity.id==key,Entity.data==row.data)
            .values(data={**row.data,**values},updated=time.time())).rowcount)


def code_text(grant,db):
    raw=db.cipher.decrypt(grant["code_encrypted"].encode()).decode()
    return raw[:4]+"-"+raw[4:]


def code_hash(db,code):
    key=db.secret("funding_hmac_key")
    if not key:return None
    return hmac.new(key.encode(),re.sub(r"[\s-]","",code).upper().encode(),hashlib.sha256).hexdigest()


def base_url(db):
    url=db.settings().get("catalog_public_url","").rstrip("/")
    p=urlsplit(url)
    if p.scheme!="https" or not p.hostname or p.hostname in {"localhost","127.0.0.1","::1"} or p.username or p.query or p.fragment:
        return None
    return url


def payments(db,gid,accepted=True):
    return [p for p in db.list("grant_payment",100000) if p.get("grant_id")==gid and (not accepted or p.get("accepted"))]


def total(db,gid):
    return sum((D(p["amount_rub"]) for p in payments(db,gid) if p.get("status") in PAID and p.get("refund_state")!="COMPLETE"),D(0))


def notify(db,grant,event,text):
    key=grant["id"]+":"+event
    if db.get("grant_message",key):return
    db.put("grant_message",key,{"state":"QUEUED","grant_id":grant["id"],"chat_id":grant["chat_id"],
        "encrypted_text":db.cipher.encrypt(text.encode()).decode()})
    db.enqueue("grant_message",{"message_id":key},key="grant_message:"+key)


def invite(db,grant,event="invite"):
    url=base_url(db)
    if not url:
        db.put("grant",grant["id"],{"notice":"Нужен публичный HTTPS-адрес каталога в настройках"});return
    code=code_text(grant,db)
    link=url+"/?"+urlencode({"collection":grant.get("collection","")})+"#code="+code
    text=db.settings().get("funding_paid_template") or DEFAULT_MESSAGE
    text=text.format_map({"amount":str(total(db,grant["id"])),"recipient":"@"+grant.get("recipient","").lstrip("@"),"url":link,"code":code})
    notify(db,grant,event,text)
    db.put("grant",grant["id"],{"notice":None})


def handle_payment(service,order,historical=False):
    """Intercept only new advertising payments and already bound top-up deals."""
    db=service.db;old=db.get("grant_payment",order["id"])
    lot=db.get("lot",order.get("lot_id")) or {}
    gid=old.get("grant_id") if old else lot.get("funding_grant_id")
    if old:
        db.put("grant_payment",old["id"],{"status":order["status"]})
        if order["status"]=="ROLLED_BACK":
            grant=db.get("grant",gid)
            if grant and grant["state"] not in {"DELIVERED","REFUNDED","REFUNDING"}:
                cas(db,"grant",gid,OPEN,{"state":"REFUNDING","notice":"Оплата отменена; проверяем остальные платежи"})
                db.enqueue("grant_refund",{"grant_id":gid},key="grant_refund:"+gid)
        return True
    if historical:return False
    if gid:
        grant=db.get("grant",gid)
        if not grant:return False
        if order["status"]!="PAID":return True
        correct=bool(order.get("buyer_id")) and order["buyer_id"]==grant["buyer_id"]
        db.put("grant_payment",order["id"],{"grant_id":gid,"amount_rub":order["sale_rub"],"status":order["status"],"buyer_id":order.get("buyer_id"),"accepted":correct,"kind":"TOPUP","lot_id":order.get("lot_id"),"chat_id":order.get("chat_id")})
        db.put("order",order["id"],{**order,"stage":"FUNDING_PAYMENT","grant_id":gid,"funding_role":"TOPUP","playerok_status":order["status"]})
        checkout=db.get("catalog_checkout",lot.get("catalog_checkout_id")) or {}
        if not correct or grant["state"] in {"DELIVERED","REFUNDING","REFUNDED"}:
            db.enqueue("grant_refund",{"grant_id":gid,"payment_id":order["id"]},key="grant_refund_payment:"+order["id"])
        elif grant["state"]=="AWAITING_TOPUP" and grant.get("checkout_id")==lot.get("catalog_checkout_id") and checkout.get("state")=="READY" and checkout.get("expires_at",0)>=time.time():
            db.put("catalog_checkout",checkout["id"],{"state":"PAID"})
            if total(db,gid)>=D(grant["selected_price"]):
                if cas(db,"grant",gid,{"AWAITING_TOPUP"},{"state":"EXECUTING"}):db.enqueue("grant_execute",{"grant_id":gid},key="grant_execute:"+gid)
        else:
            values={"state":"REVIEW","notice":"Получена поздняя доплата при активном выборе; требуется сверка"} if grant["state"] in {"PREPARING","AWAITING_TOPUP","EXECUTING"} else {"state":"AWAITING_SELECTION","selected":None,"notice":"Получена поздняя доплата. Подтвердите новый выбор"}
            cas(db,"grant",gid,OPEN,values)
            invite(db,db.get("grant",gid),"late:"+order["id"])
        return True
    if db.get("order",order["id"]):return False
    if lot.get("catalog_gift_id") or lot.get("lot_type")=="TEMPORARY" or not lot.get("pair_id") or lot.get("delivery_mode")=="manual":return False
    started_at=db.settings().get("funding_started_at")
    if not started_at or order["status"]!="PAID" or (order.get("created_at") or 0)<started_at:return False
    if not order.get("buyer_id") or not order.get("chat_id"):
        db.put("order",order["id"],{**order,"stage":"MANUAL","reason":"Не подтверждены покупатель и чат для выбора по коду"});return True
    if not db.secret("funding_hmac_key"):db.secret("funding_hmac_key",secrets.token_hex(32))
    raw="".join(secrets.choice(ALPHABET) for _ in range(8))
    digest=code_hash(db,raw)
    while db.get("grant_code",digest):
        raw="".join(secrets.choice(ALPHABET) for _ in range(8));digest=code_hash(db,raw)
    gid=order["id"]
    pair=db.get("pair",lot["pair_id"]) or {}
    grant=db.put("grant",gid,{"state":"AWAITING_SELECTION","root_order_id":gid,"buyer_id":order["buyer_id"],"chat_id":order["chat_id"],"recipient":order.get("recipient") or "","collection":pair.get("collection", ""),"revision":0,"code_encrypted":db.cipher.encrypt(raw.encode()).decode(),"created_at":time.time()})
    db.put("grant_code",digest,{"grant_id":gid})
    db.put("grant_payment",gid,{"grant_id":gid,"amount_rub":order["sale_rub"],"status":"PAID","accepted":True,"buyer_id":order["buyer_id"],"kind":"ORIGINAL","lot_id":order.get("lot_id"),"chat_id":order["chat_id"]})
    db.put("order",gid,{**order,"stage":"AWAITING_SELECTION","grant_id":gid,"funding_role":"ROOT","playerok_status":"PAID"})
    invite(db,grant)
    return True


def send_message(service,key):
    from .integrations.clients import Playerok
    db=service.db;record=db.get("grant_message",key)
    if not record or record["state"]!="QUEUED":return
    gate(db)
    grant=db.get("grant",record["grant_id"])
    source=Playerok(db).orders(deal_id=grant["root_order_id"])[0]
    if source.get("buyer_id")!=grant["buyer_id"] or source.get("chat_id")!=record["chat_id"]:raise ManualRequired("Чат покупателя не подтверждён")
    if not cas(db,"grant_message",key,{"QUEUED"},{"state":"SUBMITTED"}):return
    try:
        result=Playerok(db).send_chat_message(record["chat_id"],db.cipher.decrypt(record["encrypted_text"].encode()).decode())
        if not result.get("id"):raise RuntimeError("Не подтверждено сообщение")
        db.put("grant_message",key,{"state":"SENT","remote_id":result["id"]})
    except Exception:db.put("grant_message",key,{"state":"UNKNOWN","error":"Не подтверждена отправка сообщения; повтор требует сверки"})


def activate(db,code,ip):
    now=time.time();bucket=hashlib.sha256(ip.encode()).hexdigest()
    record=db.get("grant_attempt",bucket) or {}
    if now-record.get("start",0)>900:record={"start":now,"failures":0}
    if record.get("failures",0)>=10 or record.get("next",0)>now:raise ValueError("Слишком много попыток. Попробуйте позже")
    value=db.get("grant_code",code_hash(db,code)) or {}
    grant=db.get("grant",value.get("grant_id"))
    if not grant or grant["state"] not in OPEN:
        failures=record.get("failures",0)+1
        db.put("grant_attempt",bucket,{**record,"failures":failures,"next":now+min(60,2**failures)})
        raise ValueError("Код недействителен или уже использован")
    db.put("grant_attempt",bucket,{"start":now,"failures":0,"next":0})
    token=secrets.token_urlsafe(32)
    db.put("grant_session",hashlib.sha256(token.encode()).hexdigest(),{"grant_id":grant["id"],"expires_at":now+7*86400})
    return token


def session_grant(db,token):
    value=db.get("grant_session",hashlib.sha256((token or "").encode()).hexdigest()) or {}
    if value.get("expires_at",0)<time.time():raise ValueError("Введите код из чата Playerok")
    grant=db.get("grant",value.get("grant_id"))
    if not grant:raise ValueError("Введите код из чата Playerok")
    return grant


def public_view(db,grant):
    checkout=db.get("catalog_checkout",grant.get("checkout_id")) or {}
    return {"state":grant["state"],"paid_rub":str(total(db,grant["id"])),"recipient":grant.get("recipient"),"selected_id":(grant.get("selected") or {}).get("id"),"selected_price":grant.get("selected_price"),"checkout_id":grant.get("checkout_id"),"payment_url":checkout.get("url") if grant["state"]=="AWAITING_TOPUP" else None,"expires_at":checkout.get("expires_at"),"notice":grant.get("notice"),"gift_link":grant.get("gift_link"),"selection_locked":grant["state"] in {"PREPARING","AWAITING_TOPUP","EXECUTING","REVIEW"},"topup_rub":checkout.get("topup_rub")}


def select_gift(db,grant,gift,shown_price):
    from .catalog_pricing import price
    gate(db)
    if grant["state"]!="AWAITING_SELECTION":raise ValueError("Выбор зафиксирован до оплаты или снятия доплаты")
    quoted=price(db,gift["price_ton"])
    if quoted["sale_price"]!=shown_price:raise ValueError("Цена изменилась. Обновите карточку")
    if not grant.get("recipient"):raise ValueError("В исходном заказе нет получателя. Обратитесь к продавцу")
    rev=int(grant.get("revision",0))+1
    if not cas(db,"grant",grant["id"],{"AWAITING_SELECTION"},{"state":"PREPARING","selected":gift,"selected_price":shown_price,"revision":rev,"notice":None}):raise ValueError("Выбор уже обрабатывается")
    difference=max(D(0),D(shown_price)-total(db,grant["id"]))
    if not difference:
        cas(db,"grant",grant["id"],{"PREPARING"},{"state":"EXECUTING"})
        db.enqueue("grant_execute",{"grant_id":grant["id"]},key="grant_execute:"+grant["id"])
        return {"state":"EXECUTING"}
    key=uid()
    db.put("catalog_checkout",key,{"state":"QUEUED","gift":gift,"shown_price":shown_price,"created_at":time.time(),"funding_grant_id":grant["id"],"funding_revision":rev,"topup_rub":str(difference)})
    db.put("grant",grant["id"],{"checkout_id":key})
    db.enqueue("catalog_create",{"checkout_id":key},key="catalog_create:"+key)
    return {"state":"PREPARING","checkout_id":key}


def checkout_result(db,record):
    # Process the durable result, never a delayed snapshot from an older callback.
    record=db.get("catalog_checkout",record["id"]) or record
    gid=record.get("funding_grant_id")
    if not gid:return
    grant=db.get("grant",gid)
    if not grant or grant.get("checkout_id")!=record["id"]:return
    state=record["state"]
    if state=="READY":cas(db,"grant",gid,{"PREPARING"},{"state":"AWAITING_TOPUP"})
    elif state=="EXPIRED" or (state in {"FAILED","PRICE_CHANGED"} and not record.get("submitted_at") and not record.get("lot_id")):
        cas(db,"grant",gid,{"PREPARING","AWAITING_TOPUP"},{"state":"AWAITING_SELECTION","notice":record.get("error") or "Срок доплаты истёк. Выберите подарок заново"})
    elif state in {"UNKNOWN","FAILED","PRICE_CHANGED"}:
        cas(db,"grant",gid,{"PREPARING","AWAITING_TOPUP"},{"state":"REVIEW","notice":"Публикация доплаты требует сверки Playerok"})


def verified_payments(service,grant):
    from .integrations.clients import Playerok
    db=service.db;client=Playerok(db);rows=[]
    for payment in payments(db,grant["id"]):
        source=client.orders(deal_id=payment["id"])[0]
        if source.get("buyer_id")!=grant["buyer_id"] or source["status"] not in PAID or D(source["sale_rub"])!=D(payment["amount_rub"]):
            raise ManualRequired("Оплата или покупатель связанного заказа больше не подтверждены")
        rows.append(source)
    if not rows:raise ManualRequired("Нет подтверждённых оплат")
    return rows


def funded_fresh(service,order):
    grant=service.db.get("grant",order["funding_grant_id"])
    if not grant or grant["state"]!="EXECUTING":raise ManualRequired("Выдача по коду остановлена")
    rows=verified_payments(service,grant)
    return {"id":order["id"],"status":"PAID","sale_rub":str(sum((D(p["sale_rub"]) for p in rows),D(0))),"recipient":grant["recipient"]}


def adjust_snapshot(db,order,snapshot):
    grant=db.get("grant",order["funding_grant_id"])
    profile=grant["fee_profile"]
    fees=profile.get("fees",[])
    rate=D(snapshot["ton_rub"]);purchase=D(snapshot["purchase_rub"])
    ordinary=[f for f in fees if f.get("type")!="ton"]
    net=D(0);gross=D(0)
    for p in payments(db,grant["id"]):
        if p.get("status") not in PAID:continue
        amount=D(p["amount_rub"]);a,b=net_coefficients(ordinary,purchase,rate)
        net+=a*amount+b;gross+=amount
    ton_fee=sum((D(f["value"])*rate for f in fees if f.get("enabled",True) and f.get("type")=="ton"),D(0))
    net-=ton_fee
    gain=net-purchase-D(snapshot["transfer_rub"])
    return {**snapshot,"actual_net_rub":str(net),"actual_profit_rub":str(gain),"paid_sale_rub":str(gross),"paid_promotion_rub":"0","profitable":gain>=0}


def execute(service,gid):
    from .catalog_checkout import exact_quote
    from .catalog_pricing import context
    db=service.db;grant=db.get("grant",gid)
    if not grant or grant["state"]!="EXECUTING":return
    verified_payments(service,grant)
    if grant.get("fulfillment_order_id"):
        existing=db.get("order",grant["fulfillment_order_id"])
        if existing and existing.get("stage") not in {"MANUAL","CANCELLED"}:
            db.enqueue("process_order",{"order_id":existing["id"]},key="order:"+existing["id"]);return
    gift=grant["selected"];lot_id="grant:"+gid+":"+str(grant["revision"])
    c=context(db)
    pair_id=next((p["id"] for p in db.list("pair",100000) if p.get("collection")==gift["collection"] and p.get("model")==gift["model"]),uid())
    db.put("pair",pair_id,{"collection":gift["collection"],"model":gift["model"],"enabled":True})
    lot={"pair_id":pair_id,"delivery_mode":"telegram_nft","lot_type":"TEMPORARY","catalog_gift_id":gift["id"],"catalog_gift_slug":gift["slug"],"funding_grant_id":gid,"fee_profile_id":db.settings()["catalog_fee_profile_id"],"strategy":{"enabled":False,"premium_mode":"exclude"},"title":gift["collection"]+" #"+str(gift["number"])}
    db.put("lot",lot_id,lot)
    db.put("grant",gid,{"fee_profile":c["profile"],"fulfillment_order_id":lot_id})
    db.put("order",lot_id,{"stage":"DISCOVERED","source":"grant","funding_grant_id":gid,"lot_id":lot_id,"recipient":grant["recipient"],"sale_rub":str(total(db,gid)),"title":lot["title"],"chat_id":grant["chat_id"],"created_at":time.time()})
    try:exact_quote(service,lot)
    except ManualRequired as exc:need_choice(db,gid,str(exc));return
    db.enqueue("process_order",{"order_id":lot_id},key="order:"+lot_id)


def need_choice(db,gid,reason):
    grant=db.get("grant",gid)
    if not grant or not cas(db,"grant",gid,{"EXECUTING"},{"state":"CHOICE_REQUIRED","choice_since":time.time(),"notice":reason}):return
    grant=db.get("grant",gid)
    notify(db,grant,"choice:"+str(grant["revision"]),"Выбранный подарок сейчас нельзя выдать.\n1 — выбрать другой подарок\n2 — вернуть все оплаты по этому заказу\nКод заказа: "+code_text(grant,db))


def action_failure(db,order_id,error,submitted=False):
    order=db.get("order",order_id) or {};gid=order.get("funding_grant_id")
    if not gid:return
    if not submitted and not order.get("mrkt_gift_id") and any(word in str(error).lower() for word in ("недоступ","убыточ","подмена")):
        need_choice(db,gid,str(error))
    elif submitted:cas(db,"grant",gid,{"EXECUTING"},{"state":"REVIEW","notice":"Результат закупки или передачи требует сверки"})


def delivered(db,order):
    gid=order.get("funding_grant_id")
    if not gid:return
    if cas(db,"grant",gid,{"EXECUTING","REVIEW"},{"state":"DELIVERED","gift_link":"https://t.me/nft/"+order["gift_slug"]}):
        grant=db.get("grant",gid)
        notify(db,grant,"delivered","Подарок передан @"+grant["recipient"].lstrip("@")+"!\nhttps://t.me/nft/"+order["gift_slug"]+"\nКод заказа использован. Спасибо за покупку!")


def remote_action(service,payment,target):
    from .integrations.clients import Playerok
    db=service.db;key=payment["id"]+":"+target;client=Playerok(db)
    source=client.orders(deal_id=payment["id"])[0]
    if source.get("buyer_id")!=payment.get("buyer_id"):raise ManualRequired("Покупатель платежа не совпадает")
    if source["status"]==target or (target=="SENT" and source["status"] in {"CONFIRMED","CONFIRMED_AUTOMATICALLY"}):
        if db.get("catalog_credit_receipt",payment["id"]):db.put("catalog_credit_receipt",payment["id"],{"status":source["status"]})
        db.put("grant_action",key,{"state":"COMPLETE","payment_id":payment["id"],"target":target});return True
    old=db.get("grant_action",key)
    if old and old["state"] in {"SUBMITTED","UNKNOWN"}:return False
    if source["status"]!="PAID":return False
    gate(db)
    db.put("grant_action",key,{"state":"SUBMITTED","payment_id":payment["id"],"target":target})
    try:
        client.set_deal_status(payment["id"],target)
        fresh=client.orders(deal_id=payment["id"])[0]
        if fresh["status"]!=target:raise RuntimeError()
    except Exception:
        db.put("grant_action",key,{"state":"UNKNOWN"});return False
    if db.get("catalog_credit_receipt",payment["id"]):db.put("catalog_credit_receipt",payment["id"],{"status":fresh["status"]})
    db.put("grant_action",key,{"state":"COMPLETE"});return True


def finalize(service,order):
    db=service.db;grant=db.get("grant",order["funding_grant_id"])
    if grant["state"]!="DELIVERED":raise ManualRequired("Передача NFT ещё не подтверждена")
    results=[remote_action(service,p,"SENT") for p in payments(db,grant["id"]) if p.get("status") in PAID]
    if not all(results):raise ManualRequired("NFT выдан; подтверждение связанных сделок требует сверки")
    db.put("grant",grant["id"],{"settled":True,"notice":None})
    if order.get("stage")!="COMPLETE":
        key=order["id"]+":mark_sent"
        service.ledger(key,"revenue",order["sale_rub"],order["lot_id"],order["id"])
        service.ledger(key,"fees",D(order["sale_rub"])-D(order["snapshot"]["actual_net_rub"]),order["lot_id"],order["id"])
        db.put("order",order["id"],{"stage":"COMPLETE","completed_at":time.time()})
        db.put("processed",order["id"],{"source":"funded"})
        db.put("order",grant["root_order_id"],{"stage":"COMPLETE","completed_at":time.time()})
        service.owner_notice(order["id"],"delivered")


def request_refund(db,grant):
    for order in db.list("order",100000):
        if order.get("funding_grant_id")!=grant["id"]:continue
        ops=[o for o in db.list("operation",100000) if o.get("order_id")==order["id"] and o.get("action")=="order.buy"]
        if order.get("mrkt_gift_id") or any(o.get("submitted_at") or o.get("state") in {"COMPLETE","SUBMITTED","UNKNOWN"} for o in ops):
            raise ManualRequired("Сначала сверьте результат закупки; автоматический возврат запрещён")
    if not cas(db,"grant",grant["id"],{"CHOICE_REQUIRED","AWAITING_SELECTION","REVIEW"},{"state":"REFUNDING"}):raise ValueError("Заказ уже обрабатывается")
    db.enqueue("grant_refund",{"grant_id":grant["id"]},key="grant_refund:"+grant["id"])


def refund(service,gid,payment_id=None):
    db=service.db;grant=db.get("grant",gid)
    if not grant:return
    if not payment_id and grant["state"]!="REFUNDING":return
    rows=[db.get("grant_payment",payment_id)] if payment_id else payments(db,gid,accepted=False)
    complete=True
    for p in rows:
        if not p:continue
        if remote_action(service,p,"ROLLED_BACK"):
            db.put("grant_payment",p["id"],{"status":"ROLLED_BACK","refund_state":"COMPLETE"})
        else:
            complete=False;db.put("grant_payment",p["id"],{"refund_state":"REVIEW"})
    if payment_id:return
    db.put("grant",gid,{"state":"REFUNDED" if complete else "REFUNDING","notice":None if complete else "Часть возвратов не подтверждена. Требуется сверка Playerok"})
    if complete:notify(db,db.get("grant",gid),"refunded","Все оплаты по заказу возвращены через Playerok. Код заказа закрыт.")


def command(service,grant,choice):
    db=service.db
    if grant["state"]!="CHOICE_REQUIRED":return
    if choice=="1":
        if cas(db,"grant",grant["id"],{"CHOICE_REQUIRED"},{"state":"AWAITING_SELECTION","selected":None,"checkout_id":None,"collection":"","notice":None}):
            invite(db,db.get("grant",grant["id"]),"replacement:"+str(grant["revision"]))
    elif choice=="2":request_refund(db,grant)


def tick(service):
    from .integrations.clients import Playerok
    db=service.db
    grants=db.list("grant",100000)
    for grant in grants:
        if grant["state"] in {"PREPARING","AWAITING_TOPUP"}:
            record=db.get("catalog_checkout",grant.get("checkout_id"))
            if record:checkout_result(db,record)
        if grant["state"]=="AWAITING_SELECTION" and grant.get("notice") and base_url(db):invite(db,grant)
        if grant["state"]=="REFUNDING":refund(service,grant["id"])
        if grant["state"]=="DELIVERED" and not grant.get("settled"):
            order=db.get("order",grant.get("fulfillment_order_id"))
            if order:
                try:finalize(service,order)
                except ManualRequired:pass
    chats={g["chat_id"] for g in grants if g["state"]=="CHOICE_REQUIRED"}
    if chats:
        account=Playerok(db).account()
        for chat in chats:
            candidates=[g for g in db.list("grant",100000) if g.get("chat_id")==chat and g["state"]=="CHOICE_REQUIRED"]
            if not candidates:continue
            messages=account.get_chat_messages(chat,count=24).messages
            for message in reversed(messages):
                if getattr(message,"deleted_at",None) or db.get("grant_chat_message",message.id):continue
                try:created=datetime.fromisoformat(str(message.created_at).replace("Z","+00:00")).timestamp()
                except (ValueError,TypeError):continue
                matched=re.fullmatch(r"([12])(?:\s+([A-Za-z\s-]+))?",str(message.text or "").strip())
                if not matched:continue
                eligible=[g for g in candidates if g["buyer_id"]==str(getattr(getattr(message,"user",None),"id","")) and created>=g.get("choice_since",0)]
                if matched[2]:eligible=[g for g in eligible if code_hash(db,matched[2])==code_hash(db,code_text(g,db))]
                if len(eligible)!=1:
                    for g in eligible:notify(db,g,"ambiguous:"+str(message.id),"У вас несколько заказов. Ответьте номером действия и кодом: например, 2 XXXX-XXXX.")
                    continue
                db.put("grant_chat_message",str(message.id),{"state":"HANDLED"})
                command(service,db.get("grant",eligible[0]["id"]),matched[1])
