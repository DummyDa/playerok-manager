"""Real catalog credit: reserved funds, worker-owned purchase, verified delivery."""
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlencode

from sqlalchemy import insert, select, update

from .catalog_pricing import price
from .db import Entity, Secret, uid
from .funded_orders import ALPHABET
from .pricing import D, net_coefficients
from .safety import gate, ManualRequired

PAID={"PAID","SENT","CONFIRMED","CONFIRMED_AUTOMATICALLY"}
LOCKED={"PREPARING","AWAITING_TOPUP","EXECUTING","REVIEW"}


def purchases(payment):
    return payment.get("purchases",[])


def funds(payment):
    return max(D(0),D(payment["paid_rub"])+D(payment.get("adjustment_rub",0))-sum((D(p["price_rub"]) for p in purchases(payment)),D(0)))


def balance(payment):
    return max(D(0),funds(payment)-D(payment.get("reservation_rub",0)))


def valid(payment):
    return payment.get("state")!="DELETED" and (funds(payment)>0 or payment.get("state") in LOCKED)


def audit(db,payment,action,**values):
    db.put("catalog_credit_event",uid(),{"payment_id":payment["id"],"action":action,"at":time.time(),**values})


def save_current(db,payment,values,event=None,extra=()):
    """An unchanged state is insufficient when two devices spend the same balance."""
    previous={k:v for k,v in payment.items() if k not in {"id","updated_at"}}
    with db.tx() as session:
        changed=bool(session.execute(update(Entity).where(Entity.kind=="catalog_credit_payment",Entity.id==payment["id"],Entity.data==previous)
            .values(data={**previous,**values},updated=time.time())).rowcount)
        if changed:
            for kind,key,data in extra:session.add(Entity(kind=kind,id=key,data=data,updated=time.time()))
        if changed and event:
            session.add(Entity(kind="catalog_credit_event",id=uid(),data={"payment_id":payment["id"],"at":time.time(),"before_rub":str(balance(payment)),"after_rub":str(balance({**payment,**values})),**event},updated=time.time()))
        return changed


def digest(db, code):
    key=db.secret("catalog_credit_code_key")
    if not key:return None
    normalized=re.sub(r"[\s-]","",code).upper()
    return hmac.new(key.encode(),normalized.encode(),hashlib.sha256).hexdigest()


def catalog_link(payment,code,base="http://127.0.0.1:3180"):
    query="?"+urlencode({"collection":payment["collection"]}) if payment.get("collection") else ""
    return base.rstrip("/")+"/"+query+"#code="+code


def new_code(db):
    if not db.secret("catalog_credit_code_key"):
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        with db.tx() as session:
            session.execute(sqlite_insert(Secret).values(name="catalog_credit_code_key",value=db.cipher.encrypt(secrets.token_hex(32).encode()).decode()).on_conflict_do_nothing(index_elements=["name"]))
    code="".join(secrets.choice(ALPHABET) for _ in range(8))
    while db.get("catalog_credit_code",digest(db,code)):code="".join(secrets.choice(ALPHABET) for _ in range(8))
    return code


def create(db, amount, recipient, lot_id=None):
    if D(amount)<=0 or not recipient:raise ValueError("Укажите сумму и получателя")
    if D(amount)!=D(amount).to_integral_value():raise ValueError("Сумма кода должна быть в целых рублях для доплат Playerok")
    lot=db.get("lot",lot_id) if lot_id else {}
    if lot_id and not lot:raise ValueError("Лот не найден")
    pair=db.get("pair",lot.get("pair_id")) or {}
    code=new_code(db)
    payment=db.put("catalog_credit_payment",uid(),{"state":"AWAITING_SELECTION","test":False,"paid_rub":str(D(amount)),"initial_rub":str(D(amount)),"recipient":recipient.lstrip("@"),"lot_id":lot_id,"collection":pair.get("collection",""),"created_at":time.time(),"code_encrypted":db.cipher.encrypt(code.encode()).decode(),"payments":[{"amount_rub":str(D(amount)),"kind":"INITIAL"}]})
    db.put("catalog_credit_code",digest(db,code),{"payment_id":payment["id"]})
    audit(db,payment,"CREATED",after_rub=str(D(amount)))
    return payment,code[:4]+"-"+code[4:]


def activate(db,code,ip):
    # Serialize attempts across threads/processes, including the failure counter.
    bucket=hashlib.sha256(ip.encode()).hexdigest();now=time.time();error=None;pid=None
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        table=Entity.__table__
        previous=conn.execute(select(table.c.data).where(table.c.kind=="catalog_credit_attempt",table.c.id==bucket)).scalar_one_or_none()
        attempt=previous or {}
        if now-attempt.get("start",0)>=900:attempt={"start":now,"failures":0}
        if attempt.get("failures",0)>=10 or attempt.get("next",0)>now:
            error="Слишком много попыток. Попробуйте позже"
        else:
            hashed=digest(db,code)
            entry=conn.execute(select(table.c.data).where(table.c.kind=="catalog_credit_code",table.c.id==hashed)).scalar_one_or_none() if hashed else None
            payment=conn.execute(select(table.c.data).where(table.c.kind=="catalog_credit_payment",table.c.id==entry["payment_id"])).scalar_one_or_none() if entry else None
            if not payment or not valid(payment):
                failures=attempt.get("failures",0)+1
                attempt={**attempt,"failures":failures,"next":now+min(60,2**failures)}
                error="Код недействителен или уже использован"
            else:pid=entry["payment_id"]
        if previous:conn.execute(update(table).where(table.c.kind=="catalog_credit_attempt",table.c.id==bucket).values(data=attempt,updated=now))
        else:conn.execute(insert(table).values(kind="catalog_credit_attempt",id=bucket,data=attempt,updated=now))
        conn.commit()
    if error:raise ValueError(error)
    token=secrets.token_urlsafe(32)
    db.put("catalog_credit_session",hashlib.sha256(token.encode()).hexdigest(),{"payment_id":pid,"expires_at":now+7*86400})
    audit(db,{"id":pid},"ACTIVATED")
    return token


def session(db,token):
    record=db.get("catalog_credit_session",hashlib.sha256((token or "").encode()).hexdigest()) or {}
    if record.get("expires_at",0)<time.time():raise ValueError("Введите код из сообщения")
    payment=db.get("catalog_credit_payment",record["payment_id"])
    if not payment:raise ValueError("Оплата не найдена")
    if payment["state"]=="DELETED" and not payment.get("closed_after_delivery"):raise ValueError("Код удалён владельцем")
    if payment.get("checkout_id"):
        checkout=db.get("catalog_checkout",payment["checkout_id"])
        if checkout:checkout_result(db,checkout)
        payment=db.get("catalog_credit_payment",payment["id"])
    return payment


def end_session(db,token):
    if not token:return
    key=hashlib.sha256(token.encode()).hexdigest()
    with db.tx() as session:
        record=session.get(Entity,("catalog_credit_session",key))
        if record:session.delete(record)


def view(payment,db=None):
    checkout=db.get("catalog_checkout",payment.get("checkout_id")) if db and payment.get("checkout_id") else {}
    checkout=checkout or {}
    order=db.get("order",payment.get("fulfillment_order_id")) if db and payment.get("fulfillment_order_id") else {}
    return {**{k:payment.get(k) for k in ("state","test","paid_rub","recipient","selected","selected_price","topup_rub","collection","notice","gift_link","single_use","closed_after_delivery","catalog_price_rub")},"balance_rub":str(balance(payment)),"reserved_rub":str(D(payment.get("reservation_rub",0))),"purchased_ids":[p["gift_id"] for p in purchases(payment)],"checkout_id":payment.get("checkout_id"),"payment_url":checkout.get("url") if payment["state"]=="AWAITING_TOPUP" else None,"expires_at":checkout.get("expires_at"),"checkout_state":checkout.get("state"),"updated_gift":checkout.get("updated_gift"),"delivery_stage":(order or {}).get("stage"),"delivery_reason":(order or {}).get("reason")}


def choose(db,payment,gift,shown_price,recipient):
    gate(db,"purchases")
    if not db.settings().get("manual_gift_delivery"):
        gate(db,"transfers")
        if not db.settings().get("delivery_tested"):raise ValueError("Владелец ещё не подтвердил проверку выдачи NFT")
    current=db.get("catalog_credit_payment",payment["id"])
    if current["state"]!="AWAITING_SELECTION":raise ValueError("Предыдущий выбор уже выполняется или требует сверки")
    if gift["id"] in {p["gift_id"] for p in purchases(current)}:raise ValueError("Этот конкретный NFT уже выдан")
    if not gift.get("slug") or not re.fullmatch(r"[A-Za-z0-9]+-\d+",gift["slug"]):raise ValueError("Не подтверждён точный NFT")
    if price(db,gift["price_ton"])["sale_price"]!=shown_price:raise ValueError("Цена изменилась. Обновите карточку")
    recipient=recipient.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}",recipient):raise ValueError("Некорректный Telegram-юзернейм")
    available=funds(current)
    if available<=0:raise ValueError("Баланс использован. Создайте обычный лот для новой покупки")
    catalog_price=shown_price
    difference=max(D(0),D(shown_price)-available)
    allowance=difference if difference<=20 else D(0)
    difference=D(0) if difference<=20 else max(D(90),difference)
    # One payment/code buys one gift: all available funds are allocated.
    charged=available+difference
    if difference!=difference.to_integral_value():raise ValueError("Playerok принимает цену в целых рублях. Владелец должен скорректировать дробный остаток баланса")
    revision=int(current.get("revision",0))+1;key=uid() if difference else None
    values={"state":"PREPARING" if difference else "EXECUTING","selected":gift,"selected_price":str(charged),"catalog_price_rub":catalog_price,"single_use":True,"allowance_rub":str(allowance),"recipient":recipient,"reservation_rub":str(available),"initial_allocation_rub":str(available),"revision":revision,"checkout_id":key,"fulfillment_order_id":None,"topup_rub":str(difference),"notice":None}
    extra=[]
    if key:extra.append(("catalog_checkout",key,{"state":"QUEUED","gift":gift,"shown_price":shown_price,"created_at":time.time(),"catalog_credit_id":payment["id"],"funding_revision":revision,"topup_rub":str(difference)}))
    if not save_current(db,current,values,{"action":"TOPUP_CREATED" if difference else "FULFILLMENT_QUEUED","gift_id":gift["id"],"recipient":recipient,"topup_rub":str(difference)},extra):raise ValueError("Выбор уже изменился. Обновите страницу")
    db.enqueue("catalog_create" if key else "catalog_fulfill",{"checkout_id":key} if key else {"credit_id":payment["id"]},key=("catalog_create:"+key) if key else "catalog_fulfill:"+payment["id"]+":"+str(revision))
    return view(db.get("catalog_credit_payment",payment["id"]),db)


def checkout_result(db,record):
    cid=record.get("catalog_credit_id")
    if not cid:return
    record=db.get("catalog_checkout",record["id"]) or record
    credit=db.get("catalog_credit_payment",cid)
    if not credit or credit.get("checkout_id")!=record["id"] or credit["state"] not in {"PREPARING","AWAITING_TOPUP"}:return
    if record["state"]=="READY" and credit["state"]=="PREPARING":
        save_current(db,credit,{"state":"AWAITING_TOPUP","notice":None},{"action":"TOPUP_PUBLISHED","amount_rub":record["topup_rub"]})
    elif record["state"]=="EXPIRED" or (record["state"] in {"FAILED","PRICE_CHANGED"} and not record.get("submitted_at")):
        save_current(db,credit,{"state":"AWAITING_SELECTION","reservation_rub":"0","notice":record.get("error") or "Срок оплаты истёк. Выберите подарок заново"},{"action":"TOPUP_EXPIRED" if record["state"]=="EXPIRED" else "TOPUP_FAILED"})
    elif record["state"] in {"UNKNOWN","FAILED","PRICE_CHANGED"}:
        save_current(db,credit,{"state":"REVIEW","notice":"Результат публикации лота требует сверки владельцем"},{"action":"REVIEW_REQUIRED"})
    if record["state"]=="READY":
        from .catalog_messages import topup_requested
        topup_requested(db,db.get("catalog_credit_payment",cid),record)


def receipts(db,cid):
    return [p for p in db.list("catalog_credit_receipt",100000) if p.get("credit_id")==cid and p.get("accepted")]


def handle_payment(service,order):
    db=service.db;lot=db.get("lot",order.get("lot_id")) or {}
    previous=db.get("catalog_credit_receipt",order["id"])
    cid=previous.get("credit_id") if previous else lot.get("catalog_credit_id")
    if not cid:
        from .catalog_promo import handle_payment as promo_payment
        return promo_payment(service,order)
    if previous:
        db.put("catalog_credit_receipt",order["id"],{"status":order["status"]})
        if db.get("order",order["id"]):db.put("order",order["id"],{"playerok_status":order["status"]})
        if order["status"]=="ROLLED_BACK" and previous.get("accepted"):
            current=db.get("catalog_credit_payment",cid)
            db.put("catalog_credit_payment",cid,{"state":"DELETED" if current and current["state"]=="DELETED" else "REVIEW","notice":"Связанная доплата отменена. Нужна сверка уже выполненных закупок"})
        if order["status"]=="PAID" and previous.get("kind")!="ORIGINAL":
            current=db.get("catalog_credit_payment",cid)
            if current and not any(p.get("revision")==previous.get("revision") for p in purchases(current)):
                from .catalog_messages import topup_paid
                topup_paid(db,current,previous)
        return True
    if order["status"]!="PAID":return True
    credit=db.get("catalog_credit_payment",cid)
    checkout=db.get("catalog_checkout",lot.get("catalog_checkout_id")) or {}
    if not credit:return True
    # Deal id uniqueness and the balance update commit together.
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE");table=Entity.__table__
        if conn.execute(select(table.c.id).where(table.c.kind=="catalog_credit_receipt",table.c.id==order["id"])).first():conn.commit();return True
        raw=conn.execute(select(table.c.data).where(table.c.kind=="catalog_credit_payment",table.c.id==cid)).scalar_one()
        buyer=order.get("buyer_id")
        already_delivered=any(p.get("revision")==lot.get("funding_revision") for p in purchases(raw))
        accepted=bool(buyer) and (not raw.get("buyer_id") or raw["buyer_id"]==buyer) and raw["state"]!="DELETED" and not already_delivered and D(order["sale_rub"])==D(checkout.get("topup_rub",-1))
        receipt={"credit_id":cid,"amount_rub":order["sale_rub"],"buyer_id":buyer,"status":order["status"],"accepted":accepted,"revision":lot.get("funding_revision"),"lot_id":order.get("lot_id"),"checkout_id":lot.get("catalog_checkout_id"),"chat_id":order.get("chat_id")}
        conn.execute(insert(table).values(kind="catalog_credit_receipt",id=order["id"],data=receipt,updated=time.time()))
        values={"notice":"Оплата требует возврата/сверки: плательщик, сумма или код не подтверждены","state":"REVIEW"} if raw["state"]!="DELETED" else {}
        if accepted:
            values={"paid_rub":str(D(raw["paid_rub"])+D(order["sale_rub"])),"buyer_id":buyer}
            current=raw.get("checkout_id")==receipt["checkout_id"] and raw.get("revision")==receipt["revision"]
            if current and raw["state"] in {"PREPARING","AWAITING_TOPUP"} and checkout.get("state") in {"READY","PAID"} and checkout.get("expires_at",0)>=time.time():
                values.update(state="EXECUTING",reservation_rub=str(raw["selected_price"]),notice=None)
            elif raw["state"] in LOCKED:
                values.update(state="REVIEW",notice="Поздняя доплата при активном выборе. Нужна сверка")
            else:values.update(state="AWAITING_SELECTION",reservation_rub="0",notice="Доплата зачислена. Подтвердите новый выбор подарка")
        conn.execute(update(table).where(table.c.kind=="catalog_credit_payment",table.c.id==cid).values(data={**raw,**values},updated=time.time()))
        conn.execute(insert(table).values(kind="catalog_credit_event",id=uid(),data={"payment_id":cid,"action":"TOPUP_CONFIRMED" if accepted else "PAYMENT_REVIEW","at":time.time(),"deal_id":order["id"],"amount_rub":order["sale_rub"]},updated=time.time()))
        conn.commit()
    db.put("order",order["id"],{**order,"stage":"FUNDING_PAYMENT" if accepted else "MANUAL","catalog_topup_credit_id":cid,"playerok_status":order["status"],"reason":None if accepted else "Не выдавать: доплата требует сверки/возврата"})
    if accepted:db.put("catalog_checkout",checkout["id"],{"state":"PAID"})
    credit=db.get("catalog_credit_payment",cid)
    if credit["state"]=="EXECUTING":db.enqueue("catalog_fulfill",{"credit_id":cid},key="catalog_fulfill:"+cid+":"+str(credit["revision"]))
    from .catalog_messages import topup_paid
    topup_paid(db,credit,db.get("catalog_credit_receipt",order["id"]))
    return True


def fresh_order(service,order,allow_review=False):
    db=service.db;credit=db.get("catalog_credit_payment",order["catalog_credit_id"])
    state_ok=credit and (credit["state"]=="EXECUTING" or (allow_review and credit["state"]=="REVIEW" and (db.get("operation",order["id"]+":buy") or {}).get("state")=="COMPLETE"))
    if not state_ok or credit.get("fulfillment_order_id")!=order["id"] or funds(credit)<D(credit["selected_price"]):raise ManualRequired("Баланс или выбор заказа больше не подтверждён")
    from .integrations.clients import Playerok
    for payment in receipts(db,credit["id"]):
        source=Playerok(db).orders(deal_id=payment["id"])[0]
        if source.get("buyer_id")!=payment["buyer_id"] or source["status"] not in PAID or D(source["sale_rub"])!=D(payment["amount_rub"]):raise ManualRequired("Доплата не подтверждена свежими данными Playerok")
    return {"id":order["id"],"status":"PAID","sale_rub":str(credit["selected_price"]),"recipient":credit["recipient"]}


def adjust_snapshot(db,cid,snapshot,profile):
    credit=db.get("catalog_credit_payment",cid)
    pieces=[D(credit.get("initial_allocation_rub",credit["selected_price"]))]
    pieces += [D(p["amount_rub"]) for p in receipts(db,cid) if p.get("revision")==credit.get("revision")]
    fees=profile.get("fees",[]);rate=D(snapshot["ton_rub"])
    a,b=net_coefficients([f for f in fees if f.get("type")!="ton"],D(snapshot["purchase_rub"]),rate)
    net=sum((a*part+b for part in pieces if part>0),D(0))-sum((D(f["value"])*rate for f in fees if f.get("enabled",True) and f.get("type")=="ton"),D(0))
    profit=net-D(snapshot["purchase_rub"])-D(snapshot["transfer_rub"])
    return {**snapshot,"actual_net_rub":str(net),"actual_profit_rub":str(profit),"paid_sale_rub":str(credit["selected_price"]),"paid_promotion_rub":"0","profitable":profit>=0}


def fulfill(service,cid):
    db=service.db;credit=db.get("catalog_credit_payment",cid)
    if not credit or credit["state"]!="EXECUTING":return
    from .catalog_messages import selection_accepted
    selection_accepted(db,credit)
    if credit.get("fulfillment_order_id"):
        db.enqueue("process_order",{"order_id":credit["fulfillment_order_id"]},key="order:"+credit["fulfillment_order_id"]);return
    gift=credit["selected"];oid="catalog:"+cid+":"+str(credit["revision"])
    pair_id=next((p["id"] for p in db.list("pair",100000) if p.get("collection")==gift["collection"] and p.get("model")==gift["model"]),uid())
    db.put("pair",pair_id,{"collection":gift["collection"],"model":gift["model"],"enabled":True})
    lot={"pair_id":pair_id,"delivery_mode":"telegram_nft","lot_type":"TEMPORARY","catalog_gift_id":gift["id"],"catalog_gift_slug":gift["slug"],"catalog_credit_id":cid,"fee_profile_id":db.settings()["catalog_fee_profile_id"],"strategy":{"enabled":False,"premium_mode":"exclude"},"title":gift["collection"]+" #"+str(gift["number"])}
    order={"stage":"DISCOVERED","source":"catalog_credit","catalog_credit_id":cid,"catalog_revision":credit["revision"],"lot_id":oid,"recipient":credit["recipient"],"sale_rub":str(credit["selected_price"]),"title":lot["title"],"created_at":time.time()}
    if not save_current(db,credit,{"fulfillment_order_id":oid},{"action":"FULFILLMENT_STARTED","order_id":oid},[("lot",oid,lot),("order",oid,order)]):return
    db.enqueue("process_order",{"order_id":oid},key="order:"+oid)


def failed_order(db,oid,error,submitted=False):
    order=db.get("order",oid) or {};cid=order.get("catalog_credit_id")
    if not cid:return
    credit=db.get("catalog_credit_payment",cid)
    if not credit or credit["state"]=="DELETED" or credit.get("fulfillment_order_id")!=oid or any(p.get("order_id")==oid for p in purchases(credit)):return
    ops=[op for op in db.list("operation",100000) if op.get("order_id")==oid]
    uncertain=submitted or order.get("mrkt_gift_id") or any(op.get("submitted_at") or op.get("state") in {"UNKNOWN","SUBMITTED","COMPLETE","BROWSER_WAIT"} for op in ops)
    values={"state":"REVIEW","notice":"Результат закупки/передачи требует сверки: "+str(error)} if uncertain else {"state":"AWAITING_SELECTION","reservation_rub":"0","notice":str(error)}
    if save_current(db,credit,values,{"action":"REVIEW_REQUIRED" if uncertain else "FULFILLMENT_STOPPED","order_id":oid}) and not uncertain:
        from .catalog_messages import choice_unavailable
        choice_unavailable(db,db.get("catalog_credit_payment",cid),order)


def delivered(db,order):
    cid=order.get("catalog_credit_id")
    if not cid:return
    credit=db.get("catalog_credit_payment",cid)
    if any(p.get("order_id")==order["id"] for p in purchases(credit)):return
    if credit["state"] not in {"EXECUTING","REVIEW"}:raise ManualRequired("Код больше не ожидает подтверждения передачи")
    if credit.get("fulfillment_order_id")!=order["id"]:raise ManualRequired("Выданный NFT не совпадает с активным выбором кода")
    purchase={"order_id":order["id"],"revision":order["catalog_revision"],"gift_id":credit["selected"]["id"],"price_rub":str(order["sale_rub"]),"recipient":order["recipient"],"slug":order["gift_slug"],"at":time.time()}
    remaining=funds(credit)-D(order["sale_rub"])
    if not save_current(db,credit,{"purchases":[*purchases(credit),purchase],"reservation_rub":"0","state":"DELETED" if credit.get("single_use") else "AWAITING_SELECTION" if remaining>0 else "EXHAUSTED","closed_after_delivery":bool(credit.get("single_use")),"notice":None,"gift_link":"https://t.me/nft/"+order["gift_slug"]},{"action":"GIFT_DELIVERED","order_id":order["id"],"gift_id":purchase["gift_id"],"recipient":order["recipient"]}):raise ManualRequired("Не подтверждено списание баланса после выдачи; требуется сверка")
    from .catalog_messages import gift_delivered
    gift_delivered(db,db.get("catalog_credit_payment",cid),order)


def finalize(service,order):
    db=service.db;credit=db.get("catalog_credit_payment",order["catalog_credit_id"])
    if order.get("catalog_settled"):return
    if not any(p.get("order_id")==order["id"] for p in purchases(credit)):raise ManualRequired("Передача NFT ещё не подтверждена")
    from .funded_orders import remote_action
    for payment in receipts(db,credit["id"]):
        if payment.get("kind")=="ORIGINAL" or payment.get("revision")==order["catalog_revision"]:
            if not remote_action(service,payment,"SENT"):raise ManualRequired("NFT выдан; подтверждение доплаты Playerok требует сверки")
    key=order["id"]+":mark_sent"
    service.ledger(key,"revenue",order["sale_rub"],order["lot_id"],order["id"])
    service.ledger(key,"fees",D(order["sale_rub"])-D(order["snapshot"]["actual_net_rub"]),order["lot_id"],order["id"])
    db.put("order",order["id"],{"stage":"COMPLETE","catalog_settled":True,"completed_at":time.time()})
    db.put("processed",order["id"],{"source":"catalog_credit"})
    service.owner_notice(order["id"],"delivered")


def tick(service):
    db=service.db
    from .catalog_messages import tick as message_tick
    message_tick(db)
    for credit in db.list("catalog_credit_payment",100000):
        if credit["state"] in {"PREPARING","AWAITING_TOPUP"}:
            checkout=db.get("catalog_checkout",credit.get("checkout_id"))
            if checkout:checkout_result(db,checkout)
        if credit["state"]=="EXECUTING":
            from .catalog_messages import selection_accepted
            selection_accepted(db,credit)
            if not credit.get("fulfillment_order_id"):db.enqueue("catalog_fulfill",{"credit_id":credit["id"]},key="catalog_fulfill:"+credit["id"]+":"+str(credit["revision"]))
            else:
                order=db.get("order",credit["fulfillment_order_id"])
                if order and order["stage"] in {"DISCOVERED","AWAITING_ENABLE","WAITING_DATA","CHECK_PRICE","TRANSFERRED"}:db.enqueue("process_order",{"order_id":order["id"]},key="order:"+order["id"])
                if order and order["stage"]=="MANUAL":failed_order(db,order["id"],order.get("reason","Нужна сверка операции"))
        for purchase in purchases(credit):
            order=db.get("order",purchase["order_id"])
            if order:
                from .catalog_messages import gift_delivered
                gift_delivered(db,credit,order)
            if not order or order.get("catalog_settled") or not order.get("snapshot"):continue
            if service.delivery_confirmed(order):
                try:
                    finalize(service,order)
                    op=db.get("operation",order["id"]+":mark_sent")
                    if op:db.put("operation",op["id"],{"state":"COMPLETE","reconciled":True,"error":None})
                except ManualRequired:pass


def change_balance(db,payment,amount,note=""):
    if payment["state"]=="DELETED":raise ValueError("Код удалён")
    if payment["state"] in LOCKED:raise ValueError("Дождитесь завершения выдачи/доплаты или сверки результата")
    if D(amount)<0:raise ValueError("Баланс не может быть отрицательным")
    if D(amount)!=D(amount).to_integral_value():raise ValueError("Баланс должен быть в целых рублях для доплат Playerok")
    values={"adjustment_rub":str(D(payment.get("adjustment_rub",0))+D(amount)-balance(payment)),"state":"AWAITING_SELECTION" if D(amount)>0 else "EXHAUSTED"}
    if not save_current(db,payment,values,{"action":"BALANCE_CHANGED","note":note}):raise ValueError("Баланс уже изменился. Обновите список")
    return db.get("catalog_credit_payment",payment["id"])


def remove(db,payment):
    if payment["state"]=="DELETED":return payment
    if payment["state"]=="EXECUTING":raise ValueError("Сначала дождитесь остановки активной выдачи")
    if not save_current(db,payment,{"state":"DELETED","reservation_rub":"0","deleted_at":time.time()},{"action":"DELETED","previous_state":payment["state"],"reserved_before_rub":payment.get("reservation_rub","0")}):raise ValueError("Заказ изменился. Обновите список")
    if payment.get("checkout_id"):
        checkout=db.get("catalog_checkout",payment["checkout_id"])
        if checkout and checkout["state"]=="READY":db.put("catalog_checkout",checkout["id"],{"expires_at":time.time()});db.enqueue("catalog_expire")
    return db.get("catalog_credit_payment",payment["id"])


