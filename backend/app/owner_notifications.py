"""Compact owner receipts; separate from buyer chat templates."""
from datetime import datetime, timezone, timedelta
from html import escape
import re
import time

from .db import Entity
from .pricing import D


def amount(value, signed=False):
    number=D(value)
    text=f"{number:,.2f}".replace(","," ").replace(".",",").rstrip("0").rstrip(",")
    return ("+" if signed and number>0 else "")+text


def render(db, order, event, test=False):
    snapshot=order.get("snapshot") or {}
    pair=db.get("pair",(db.get("lot",order.get("lot_id")) or {}).get("pair_id")) or {}
    slug=order.get("gift_slug","")
    match=re.fullmatch(r"([A-Za-z0-9]+)-(\d+)",slug)
    name=(pair.get("collection") or (match.group(1) if match else "NFT"))+(" #"+match.group(2) if match else "")
    if pair.get("model"):name+=" · "+pair["model"]
    ledger=[e for e in db.list("ledger",100000) if e.get("order_id")==order["id"]]
    sums={kind:sum((D(e["amount_rub"]) for e in ledger if e.get("type")==kind),D(0)) for kind in ("revenue","purchase","transfer","fees","promotion")}
    rate=D(snapshot.get("ton_rub") or 0)
    def rub(value, signed=False):
        return amount(value,signed)+" ₽"
    def equivalent(value):
        return " · ≈ "+amount(D(value)/rate)+" TON" if rate>0 else ""
    title="✅ Подарок выдан" if event=="delivered" else "🛒 Подарок куплен"
    if test:title="🧪 ТЕСТ · "+title
    recipient="@"+str(order.get("recipient") or "").lstrip("@") if order.get("recipient") else "Не указан"
    lines=[f"<b>{title}</b> · <code>{escape(order['id'][-8:].upper())}</code>",
           "",f"🎁 <b>{escape(name)}</b>","👤 "+escape(recipient),""]
    if event=="delivered":
        revenue=sums["revenue"]
        costs=sum((sums[k] for k in ("purchase","transfer","fees","promotion")),D(0))
        profit=revenue-costs
        percent=" · "+amount(profit/revenue*100)+"%" if revenue>0 else ""
        lines.append("<blockquote>💰 Выручка: <b>"+rub(revenue)+equivalent(revenue)+"</b>\n"
                     "📦 Все расходы: <b>"+rub(costs)+equivalent(costs)+"</b>\n"
                     "📈 Чистая прибыль: <b>"+rub(profit,True)+equivalent(profit)+percent+"</b></blockquote>")
        start=datetime.now(timezone(timedelta(hours=3))).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        completed={o["id"] for o in db.list("order",100000) if o.get("stage")=="COMPLETE" and o.get("completed_at",0)>=start}
        daily=sum((D(e["amount_rub"]) if e.get("type")=="revenue" else -D(e["amount_rub"]) for e in db.list("ledger",100000) if e.get("order_id") in completed),D(0))
        lines.extend(["",f"📊 За сутки: {len(completed)} выдано · прибыль <b>{rub(daily,True)}</b>","<i>По учтённым расходам</i>"])
    else:
        purchase=sums["purchase"]
        ton=snapshot.get("quote_ton")
        purchase_text=(amount(ton)+" TON · " if ton is not None else "")+rub(purchase)
        profit=snapshot.get("actual_profit_rub")
        lines.append("<blockquote>📦 Закупка: <b>"+purchase_text+"</b>\n💰 Оплачено: <b>"+rub(order.get("sale_rub",0))+"</b>"+
                     ("\n📈 Ожидаемая прибыль: <b>"+rub(profit,True)+"</b>" if profit is not None else "")+"</blockquote>")
        lines.extend(["","<i>Следующий шаг — передача покупателю</i>"])
    payload={"text":"\n".join(lines),"parse_mode":"HTML","link_preview_options":{"is_disabled":True}}
    if re.fullmatch(r"[0-9a-fA-F-]{36}",order["id"]):
        payload["reply_markup"]={"inline_keyboard":[[{"text":"📦 Заказ на Playerok","url":"https://playerok.com/deal/"+order["id"]}]]}
    return payload


def queue(db, order, event):
    if not db.settings()["notifications_enabled"]:return
    key="owner:"+order["id"]+":"+event
    if db.get("notification",key):return
    db.put("notification",key,{"event":event,"order_id":order["id"],"state":"PENDING","payload":render(db,order,event)})


def send_pending(db, bot):
    # Retire old technical alerts and never replay a possibly submitted message.
    for item in db.list("notification",100000):
        if item.get("event") not in {"purchased","delivered"} and item.get("state")=="PENDING":
            db.put("notification",item["id"],{"state":"SUPPRESSED"})
    chat=db.settings().get("owner_chat_id")
    if not chat:return
    pending=sorted((n for n in db.list("notification",100000) if n.get("state")=="PENDING" and n.get("event") in {"purchased","delivered"}),key=lambda n:n["updated_at"])
    for item in pending[:5]:
        with db.tx() as session:
            row=session.get(Entity,("notification",item["id"]))
            if not row or row.data.get("state")!="PENDING":continue
            row.data={**row.data,"state":"SUBMITTED","submitted_at":time.time()}
        try:
            result=bot.request("sendMessage",{**item["payload"],"chat_id":chat})
        except Exception as exc:
            db.put("notification",item["id"],{"state":"UNKNOWN","error":db.redact(exc)})
            continue
        db.put("notification",item["id"],{"state":"SENT","message_id":result.get("message_id")})
