"""Read model: one catalog balance, its payments and all deliveries in one order card."""
import json
from .catalog_credit import balance
from .pricing import D


def review_reason(db,wallet):
    reason=wallet.get("notice")
    if wallet.get("state")!="REVIEW":return reason
    checkout=db.get("catalog_checkout",wallet.get("checkout_id","")) or {}
    if checkout.get("state")!="UNKNOWN" or not checkout.get("error"):return reason
    error=db.redact(checkout["error"])
    # Preserve the useful platform rejection without a GraphQL stack/payload.
    start=error.find("{")
    if start>=0:
        try:
            data,_=json.JSONDecoder().raw_decode(error[start:])
            messages=[str(e["message"]) for e in data.get("errors",[]) if isinstance(e,dict) and e.get("message")]
            if messages:error="; ".join(messages)
        except (ValueError,TypeError,AttributeError):pass
    label="Лот доплаты на "+str(checkout["topup_rub"])+" ₽" if checkout.get("topup_rub") else "Публикация лота"
    return (reason or "Результат требует сверки владельцем")+". "+label+". Причина: "+error


def grouped_orders(db,orders,ledger):
    groups=[];hidden=set()
    receipts=db.list("catalog_credit_receipt",100000)
    wallets=db.list("catalog_credit_payment",100000)
    for wallet in wallets:
        if wallet.get("test") is True:continue
        cid=wallet["id"]
        linked=[o for o in orders if cid in (o.get("catalog_credit_id"),o.get("catalog_root_credit_id"),o.get("catalog_topup_credit_id"))]
        payments=[p for p in receipts if p.get("credit_id")==cid]
        if not linked and not payments and not wallet.get("purchases"):continue
        deliveries=[o for o in linked if o.get("catalog_credit_id")==cid]
        delivery_ids={o["id"] for o in deliveries}
        entries=[e for e in ledger if e.get("order_id") in delivery_ids]
        settled={e.get("order_id") for e in entries if e.get("type")=="revenue"}
        profit=sum((D(e["amount_rub"]) if e.get("type")=="revenue" else -D(e["amount_rub"]) for e in entries if e.get("order_id") in settled),D(0))
        totals={k:str(sum((D(e["amount_rub"]) for e in entries if e.get("type")==k),D(0))) for k in ("revenue","purchase","transfer","fees","promotion")}
        accepted=[p for p in payments if p.get("accepted")]
        paid=sum((D(p["amount_rub"]) for p in accepted),D(0))
        refunded=sum((D(p["amount_rub"]) for p in accepted if p.get("status")=="ROLLED_BACK"),D(0))
        manual=D(wallet.get("initial_rub",wallet.get("paid_rub",0))) if wallet.get("source")!="playerok_promotional" else D(0)
        state=wallet["state"]
        stage={"EXHAUSTED":"COMPLETE","DELETED":"CANCELLED","REVIEW":"MANUAL","PREPARING":"AWAITING_TOPUP"}.get(state,state)
        active=next((o for o in deliveries if o["id"]==wallet.get("fulfillment_order_id")),None)
        if state=="EXECUTING" and active:stage=active["stage"]
        if active and active.get("stage")!="COMPLETE" and any(p.get("order_id")==active["id"] for p in wallet.get("purchases",[])):
            stage=active["stage"]
        if wallet.get("closed_after_delivery"):stage=active.get("stage","COMPLETE") if active else "COMPLETE"
        oid=wallet.get("root_order_id") or "catalog-group:"+cid
        group={"id":oid,"catalog_group":True,"catalog_credit_id":cid,"title":"Заказ каталога · "+(wallet.get("collection") or "Все коллекции"),"stage":stage,"credit_state":state,"source":"catalog_credit","recipient":wallet.get("recipient"),"created_at":wallet.get("created_at",0),"updated_at":max([wallet.get("updated_at",0)]+[o.get("updated_at",0) for o in linked]),"sale_rub":str(paid+manual),"paid_rub":str(paid),"manual_credit_rub":str(manual),"adjustment_rub":wallet.get("adjustment_rub","0"),"refunded_rub":str(refunded),"balance_rub":str(balance(wallet)),"reserved_rub":str(D(wallet.get("reservation_rub",0))),"selected":wallet.get("selected"),"selected_price":wallet.get("selected_price"),"payments":[{k:p.get(k) for k in ("id","kind","amount_rub","status","accepted","revision","created_at")} for p in payments],"deliveries":deliveries,"totals":totals,"profit_rub":str(profit) if settled else None,"profit_actual":bool(settled),"reason":review_reason(db,wallet),"retry_allowed":False,"retry_reason":"Действия доступны у конкретной выдачи","delivery_count":len(wallet.get("purchases",[]))}
        groups.append(group);hidden.update(o["id"] for o in linked)
    return [o for o in orders if o["id"] not in hidden]+groups
