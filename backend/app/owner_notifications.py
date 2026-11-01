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
    if event=="paid":
        recipient="@"+str(order.get("recipient") or "").lstrip("@") if order.get("recipient") else "Не указан"
        buyer=order.get("buyer_username") or order.get("buyer_id") or "Не определён"
        return {"text":"💳 <b>Заказ оплачен</b>\n\n🎁 "+escape(order.get("title") or "Товар")+"\n💰 <b>"+amount(order.get("sale_rub",0))+" ₽</b>\n👤 Покупатель: "+escape(str(buyer))+"\n📨 Получатель: "+escape(recipient),"parse_mode":"HTML","reply_markup":{"inline_keyboard":[[{"text":"Открыть заказ","url":"https://playerok.com/deal/"+order["id"]}]]}}
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
    link_order=order.get("funding_grant_id") or order["id"]
    if re.fullmatch(r"[0-9a-fA-F-]{36}",link_order):
        payload["reply_markup"]={"inline_keyboard":[[{"text":"📦 Заказ на Playerok","url":"https://playerok.com/deal/"+link_order}]]}
    if not test and event=="purchased" and db.settings().get("manual_gift_delivery") and order.get("stage")!="COMPLETE":
        from .owner_delivery_buttons import button
        payload.setdefault("reply_markup",{"inline_keyboard":[]})["inline_keyboard"].append([button(db,order)])
    return payload


def queue(db, order, event):
    if not db.settings()["notifications_enabled"]:return
    key="owner:"+order["id"]+":"+event
    if db.get("notification",key):return
    db.put("notification",key,{"event":event,"order_id":order["id"],"state":"PENDING","payload":render(db,order,event)})


def send_pending(db, bot):
    # Retire old technical alerts and never replay a possibly submitted message.
    for item in db.list("notification",100000):
        if item.get("event") not in {"paid","purchased","delivered","support"} and item.get("state")=="PENDING":
            db.put("notification",item["id"],{"state":"SUPPRESSED"})
    chat=db.settings().get("owner_chat_id")
    if not chat:return
    if db.settings().get("manual_gift_delivery"):
        from .manual_delivery import allowed
        for item in db.list("notification",100000):
            if item.get("event")!="purchased" or item.get("state")!="SENT" or not item.get("message_id") or item.get("manual_button_skip"):continue
            keyboard=item.get("payload",{}).get("reply_markup",{}).get("inline_keyboard",[])
            if any(str(b.get("callback_data","")).startswith("md:") for row in keyboard for b in row):continue
            order=db.get("order",item.get("order_id"))
            if not allowed(db,order):continue
            payload=render(db,order,"purchased")
            try:bot.request("editMessageReplyMarkup",{"chat_id":chat,"message_id":item["message_id"],"reply_markup":payload["reply_markup"]})
            except Exception as exc:
                db.put("notification",item["id"],{"manual_button_skip":True,"manual_button_error":db.redact(exc)})
                continue
            db.put("notification",item["id"],{"payload":{**item["payload"],"reply_markup":payload["reply_markup"]}})
            break
    pending=sorted((n for n in db.list("notification",100000) if n.get("state")=="PENDING" and n.get("event") in {"paid","purchased","delivered","support"}),key=lambda n:n["updated_at"])
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
