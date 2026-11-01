"""Isolated paid-choice rehearsal. No worker tasks or marketplace writes."""
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlencode

from sqlalchemy import insert, select, update

from .catalog_pricing import price
from .db import Entity, uid
from .funded_orders import ALPHABET
from .pricing import D


def purchases(payment):
    if "purchases" in payment:return payment["purchases"]
    # Preserve the remainder of test purchases completed before balance support.
    if payment.get("state")=="TEST_COMPLETE" and payment.get("selected") and payment.get("selected_price") is not None:
        return [{"gift_id":payment["selected"]["id"],"price_rub":str(payment["selected_price"])}]
    return []


def balance(payment):
    return max(D(0),D(payment["paid_rub"])+D(payment.get("adjustment_rub",0))-sum((D(p["price_rub"]) for p in purchases(payment)),D(0)))


def valid(payment):
    return payment.get("state")!="DELETED" and (balance(payment)>0 or payment.get("state")=="AWAITING_TOPUP")


def audit(db,payment,action,**values):
    db.put("catalog_test_event",uid(),{"payment_id":payment["id"],"action":action,"at":time.time(),**values})


def save_current(db,payment,values,event=None):
    """An unchanged state is insufficient when two devices spend the same balance."""
    previous={k:v for k,v in payment.items() if k not in {"id","updated_at"}}
    with db.tx() as session:
        changed=bool(session.execute(update(Entity).where(Entity.kind=="catalog_test_payment",Entity.id==payment["id"],Entity.data==previous)
            .values(data={**previous,**values},updated=time.time())).rowcount)
        if changed and event:
            session.add(Entity(kind="catalog_test_event",id=uid(),data={"payment_id":payment["id"],"at":time.time(),"before_rub":str(balance(payment)),"after_rub":str(balance({**payment,**values})),**event},updated=time.time()))
        return changed


def digest(db, code):
    key=db.secret("catalog_test_code_key")
    if not key:return None
    normalized=re.sub(r"[\s-]","",code).upper()
    return hmac.new(key.encode(),normalized.encode(),hashlib.sha256).hexdigest()


def catalog_link(payment,code,base="http://127.0.0.1:3180"):
    query="?"+urlencode({"collection":payment["collection"]}) if payment.get("collection") else ""
    return base.rstrip("/")+"/"+query+"#code="+code


def create(db, amount, recipient, lot_id=None):
    if D(amount)<=0 or not recipient:raise ValueError("Укажите сумму и получателя")
    lot=db.get("lot",lot_id) if lot_id else {}
    if lot_id and not lot:raise ValueError("Лот не найден")
    pair=db.get("pair",lot.get("pair_id")) or {}
    if not db.secret("catalog_test_code_key"):db.secret("catalog_test_code_key",secrets.token_hex(32))
    code="".join(secrets.choice(ALPHABET) for _ in range(8))
    while db.get("catalog_test_code",digest(db,code)):
        code="".join(secrets.choice(ALPHABET) for _ in range(8))
    payment=db.put("catalog_test_payment",uid(),{"state":"AWAITING_SELECTION","test":True,"paid_rub":str(D(amount)),"initial_rub":str(D(amount)),"recipient":recipient.lstrip("@"),"lot_id":lot_id,"collection":pair.get("collection",""),"created_at":time.time(),"code_encrypted":db.cipher.encrypt(code.encode()).decode(),"payments":[{"amount_rub":str(D(amount)),"kind":"INITIAL"}]})
    db.put("catalog_test_code",digest(db,code),{"payment_id":payment["id"]})
    audit(db,payment,"CREATED",after_rub=str(D(amount)))
    return payment,code[:4]+"-"+code[4:]


def activate(db,code,ip):
    # Serialize attempts across threads/processes, including the failure counter.
    bucket=hashlib.sha256(ip.encode()).hexdigest();now=time.time();error=None;pid=None
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        table=Entity.__table__
        previous=conn.execute(select(table.c.data).where(table.c.kind=="catalog_test_attempt",table.c.id==bucket)).scalar_one_or_none()
        attempt=previous or {}
        if now-attempt.get("start",0)>=900:attempt={"start":now,"failures":0}
        if attempt.get("failures",0)>=10 or attempt.get("next",0)>now:
            error="Слишком много попыток. Попробуйте позже"
        else:
            hashed=digest(db,code)
            entry=conn.execute(select(table.c.data).where(table.c.kind=="catalog_test_code",table.c.id==hashed)).scalar_one_or_none() if hashed else None
            payment=conn.execute(select(table.c.data).where(table.c.kind=="catalog_test_payment",table.c.id==entry["payment_id"])).scalar_one_or_none() if entry else None
            if not payment or not valid(payment):
                failures=attempt.get("failures",0)+1
                attempt={**attempt,"failures":failures,"next":now+min(60,2**failures)}
                error="Код недействителен или уже использован"
            else:pid=entry["payment_id"]
        if previous:conn.execute(update(table).where(table.c.kind=="catalog_test_attempt",table.c.id==bucket).values(data=attempt,updated=now))
        else:conn.execute(insert(table).values(kind="catalog_test_attempt",id=bucket,data=attempt,updated=now))
        conn.commit()
    if error:raise ValueError(error)
    token=secrets.token_urlsafe(32)
    db.put("catalog_test_session",hashlib.sha256(token.encode()).hexdigest(),{"payment_id":pid,"expires_at":now+7*86400})
    audit(db,{"id":pid},"ACTIVATED")
    return token


def session(db,token):
    record=db.get("catalog_test_session",hashlib.sha256((token or "").encode()).hexdigest()) or {}
    if record.get("expires_at",0)<time.time():raise ValueError("Введите код из сообщения")
    payment=db.get("catalog_test_payment",record["payment_id"])
    if not payment:raise ValueError("Оплата не найдена")
    if payment["state"]=="DELETED":raise ValueError("Код удалён владельцем")
    if payment["state"]=="AWAITING_TOPUP" and payment.get("expires_at",0)<=time.time():
        save_current(db,payment,{"state":"AWAITING_SELECTION","selected":None,"selected_price":None,"topup_rub":None,"expires_at":None},{"action":"TOPUP_EXPIRED"})
        payment=db.get("catalog_test_payment",payment["id"])
    return payment


def end_session(db,token):
    if not token:return
    key=hashlib.sha256(token.encode()).hexdigest()
    with db.tx() as session:
        record=session.get(Entity,("catalog_test_session",key))
        if record:session.delete(record)


def view(payment):
    return {**{k:payment.get(k) for k in ("state","test","paid_rub","recipient","selected","selected_price","topup_rub","expires_at","collection")},"balance_rub":str(balance(payment)),"purchased_ids":[p["gift_id"] for p in purchases(payment)]}


def choose(db,payment,gift,shown_price,recipient=None):
    current=db.get("catalog_test_payment",payment["id"])
    if current["state"] not in {"AWAITING_SELECTION","TEST_COMPLETE"} or (current["state"]=="TEST_COMPLETE" and balance(current)<=0):raise ValueError("Выбор уже зафиксирован до оплаты или окончания 15 минут")
    if gift["id"] in {p["gift_id"] for p in purchases(current)}:raise ValueError("Этот подарок уже получен. Выберите другой")
    quote=price(db,gift["price_ton"])
    if quote["sale_price"]!=shown_price:raise ValueError("Цена изменилась. Обновите карточку")
    confirmed=(recipient if recipient is not None else current["recipient"]).strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}",confirmed):raise ValueError("Введите Telegram-юзернейм: 5–32 символа, латинские буквы, цифры и подчёркивание")
    difference=max(D(0),D(shown_price)-balance(current))
    values={"state":"AWAITING_TOPUP" if difference else "TEST_COMPLETE","recipient":confirmed,"selected":gift,"selected_price":shown_price,"topup_rub":str(difference),"expires_at":time.time()+900 if difference else None}
    values["purchases"]=purchases(current)+([] if difference else [{"gift_id":gift["id"],"price_rub":str(shown_price),"recipient":confirmed}])
    if not save_current(db,current,values,{"action":"TOPUP_CREATED" if difference else "GIFT_SELECTED","gift_id":gift["id"],"price_rub":str(shown_price),"topup_rub":str(difference),"recipient":confirmed,"previous_recipient":current["recipient"]}):raise ValueError("Выбор уже обрабатывается")
    return view(db.get("catalog_test_payment",payment["id"]))


def confirm_topup(db,payment):
    if payment["state"]!="AWAITING_TOPUP" or payment.get("expires_at",0)<=time.time():raise ValueError("Доплата уже закрыта или истекла")
    values={"state":"TEST_COMPLETE","paid_rub":str(D(payment["paid_rub"])+D(payment["topup_rub"])),"payments":[*payment["payments"],{"amount_rub":payment["topup_rub"],"kind":"TOPUP"}],"purchases":[*purchases(payment),{"gift_id":payment["selected"]["id"],"price_rub":str(payment["selected_price"]),"recipient":payment["recipient"]}]}
    if not save_current(db,payment,values,{"action":"TOPUP_CONFIRMED","gift_id":payment["selected"]["id"],"amount_rub":payment["topup_rub"]}):raise ValueError("Доплата уже обработана")
    return view(db.get("catalog_test_payment",payment["id"]))


def change_balance(db,payment,amount,note=""):
    if D(amount)<0:raise ValueError("Баланс не может быть отрицательным")
    if payment["state"]=="DELETED":raise ValueError("Код удалён")
    if payment["state"]=="AWAITING_TOPUP" and payment.get("expires_at",0)>time.time():raise ValueError("Дождитесь оплаты или истечения срока зафиксированной доплаты")
    values={"adjustment_rub":str(D(payment.get("adjustment_rub",0))+D(amount)-balance(payment)),"purchases":purchases(payment)}
    if payment["state"]=="AWAITING_TOPUP":values.update(state="AWAITING_SELECTION",selected=None,selected_price=None,topup_rub=None,expires_at=None)
    if D(amount)==0:values["state"]="TEST_COMPLETE"
    if not save_current(db,payment,values,{"action":"BALANCE_CHANGED","note":note}):raise ValueError("Баланс уже изменился. Обновите список")
    return db.get("catalog_test_payment",payment["id"])


def remove(db,payment):
    if payment["state"]=="DELETED":return payment
    if not save_current(db,payment,{"state":"DELETED","deleted_at":time.time(),"purchases":purchases(payment)},{"action":"DELETED"}):raise ValueError("Заказ изменился. Обновите список")
    return db.get("catalog_test_payment",payment["id"])
