"""Turn new paid promotional lots into real catalog credit exactly once."""
import time
from sqlalchemy import insert,select
from . import catalog_credit as credit
from .db import Entity,uid
from .pricing import D
from .safety import is_nft_lot


def handle_payment(service,order):
    db=service.db;settings=db.settings();started=settings.get("catalog_promo_started_at")
    if not started or not settings.get("orders_baselined") or order["status"]!="PAID" or (order.get("created_at") or 0)<started:return False
    lot=db.get("lot",order.get("lot_id")) or {}
    if not is_nft_lot(lot) or lot.get("catalog_gift_id") or lot.get("lot_type")=="TEMPORARY" or lot.get("catalog_credit_id"):return False
    existing=db.get("order",order["id"])
    if db.get("processed",order["id"]):return False
    if existing and (existing.get("source")=="legacy" or existing.get("stage")!="HISTORICAL" or (existing.get("playerok_status") or existing.get("status")) in credit.PAID):return False
    if not order.get("buyer_id") or not order.get("chat_id"):
        db.put("order",order["id"],{**order,"stage":"MANUAL","reason":"Не подтверждены покупатель и чат оплаченного рекламного лота"});return True
    if D(order["sale_rub"])<=0:return True
    code=credit.new_code(db);cid="promo:"+order["id"]
    pair=db.get("pair",lot["pair_id"]) or {}
    data={"state":"AWAITING_SELECTION","test":False,"source":"playerok_promotional","root_order_id":order["id"],"paid_rub":order["sale_rub"],"initial_rub":order["sale_rub"],"recipient":order.get("recipient") or "","lot_id":lot["id"],"collection":pair.get("collection",""),"buyer_id":order["buyer_id"],"buyer_username":order.get("buyer_username"),"chat_id":order["chat_id"],"created_at":time.time(),"code_encrypted":db.cipher.encrypt(code.encode()).decode(),"payments":[{"amount_rub":order["sale_rub"],"kind":"ORIGINAL","deal_id":order["id"]}]}
    receipt={"credit_id":cid,"amount_rub":order["sale_rub"],"buyer_id":order["buyer_id"],"chat_id":order["chat_id"],"accepted":True,"kind":"ORIGINAL","status":order["status"],"revision":0,"lot_id":lot["id"]}
    root={**order,"stage":"AWAITING_SELECTION","catalog_root_credit_id":cid,"playerok_status":order["status"]}
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE");table=Entity.__table__
        if conn.execute(select(table.c.id).where(table.c.kind=="catalog_credit_receipt",table.c.id==order["id"])).first():conn.commit();return True
        # Buyer ID is authoritative: renaming a Playerok account must not lose its balance.
        reused=False
        for row in conn.execute(select(table.c.id,table.c.data).where(table.c.kind=="catalog_credit_payment")).all():
            old=row.data
            if old.get("source")!="playerok_promotional" or old.get("buyer_id")!=order["buyer_id"] or old.get("state") not in {"AWAITING_SELECTION","EXHAUSTED"}:continue
            try:
                saved_code=db.cipher.decrypt(old["code_encrypted"].encode()).decode()
                mapping=conn.execute(select(table.c.data).where(table.c.kind=="catalog_credit_code",table.c.id==credit.digest(db,saved_code))).scalar_one_or_none()
                if not mapping or mapping.get("payment_id")!=row.id:continue
            except (KeyError,ValueError,UnicodeError):continue
            except Exception as exc:
                from cryptography.fernet import InvalidToken
                if isinstance(exc,InvalidToken):continue
                raise
            cid=row.id;reused=True
            data={**old,"collection":pair.get("collection",old.get("collection","")),"paid_rub":str(D(old["paid_rub"])+D(order["sale_rub"])),"buyer_username":order.get("buyer_username") or old.get("buyer_username"),"chat_id":order["chat_id"],"state":"AWAITING_SELECTION","notice":None,"payments":[*old.get("payments",[]),{"amount_rub":order["sale_rub"],"kind":"ORIGINAL","deal_id":order["id"]}]}
            receipt["credit_id"]=cid;root["catalog_root_credit_id"]=cid
            conn.execute(table.update().where(table.c.kind=="catalog_credit_payment",table.c.id==cid).values(data=data,updated=time.time()))
            break
        if existing:conn.execute(table.delete().where(table.c.kind=="order",table.c.id==order["id"]))
        for kind,key,value in [("catalog_credit_payment",cid,data),("catalog_credit_code",credit.digest(db,code),{"payment_id":cid}),("catalog_credit_receipt",order["id"],receipt),("order",order["id"],root),("catalog_credit_event",uid(),{"payment_id":cid,"action":"PROMOTIONAL_PAID","at":time.time(),"deal_id":order["id"],"after_rub":order["sale_rub"]})]:
            if reused and kind in {"catalog_credit_payment","catalog_credit_code"}:continue
            conn.execute(insert(table).values(kind=kind,id=key,data=value,updated=time.time()))
        conn.commit()
    from .catalog_messages import promo_paid
    promo_paid(db,db.get("catalog_credit_payment",cid),db.get("catalog_credit_receipt",order["id"]))
    return True
