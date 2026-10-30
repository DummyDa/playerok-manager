import time
from unittest.mock import Mock

from backend.app.owner_notifications import queue, render, send_pending
from backend.app.service import Service


def prepare(db):
    db.configure(notifications_enabled=True,owner_chat_id=77)
    db.put("pair","p",{"collection":"Chill <Flame>","model":"Ghost & Light"})
    db.put("lot","l",{"pair_id":"p"})
    order=db.put("order","1f1d3c6b-1ef6-6e80-520e-8ce4b21f0952",{
        "lot_id":"l","stage":"COMPLETE","gift_slug":"ChillFlame-306647","recipient":"buyer",
        "sale_rub":"200","completed_at":time.time(),"snapshot":{"ton_rub":"100","quote_ton":"1","actual_profit_rub":"40"}})
    for kind,amount in [("revenue",200),("purchase",100),("transfer",25),("fees",20),("promotion",13)]:
        db.put("ledger",kind,{"order_id":order["id"],"type":kind,"amount_rub":str(amount)})
    return order


def test_delivered_receipt_escapes_data_and_uses_actual_costs(db):
    order=prepare(db);payload=render(db,order,"delivered")
    assert "Chill &lt;Flame&gt; #306647 · Ghost &amp; Light" in payload["text"]
    assert "158 ₽" in payload["text"] and "+42 ₽" in payload["text"]
    assert "21%" in payload["text"] and "1 выдано" in payload["text"]
    assert payload["parse_mode"]=="HTML" and payload["link_preview_options"]["is_disabled"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["url"].endswith(order["id"])


def test_only_two_owner_events_and_no_old_technical_alerts(db):
    order=prepare(db)
    Service(db).notice("quote:p","Price changed","details")
    assert db.list("notification")==[]
    queue(db,order,"purchased");queue(db,order,"purchased");queue(db,order,"delivered")
    db.put("notification","old",{"state":"PENDING","title":"Old technical message"})
    bot=Mock();bot.request.return_value={"message_id":123}
    send_pending(db,bot);send_pending(db,bot)
    assert bot.request.call_count==2
    assert all(call.args[1]["chat_id"]==77 for call in bot.request.call_args_list)
    assert db.get("notification","old")["state"]=="SUPPRESSED"
    assert all(n["state"]=="SENT" for n in db.list("notification") if n.get("event"))


def test_owner_notifications_respect_setting_and_do_not_repeat_uncertain_send(db):
    order=prepare(db)
    db.configure(notifications_enabled=False);queue(db,order,"purchased")
    assert db.list("notification")==[]
    db.configure(notifications_enabled=True);queue(db,order,"purchased")
    bot=Mock();bot.request.side_effect=TimeoutError("unknown delivery")
    send_pending(db,bot);send_pending(db,bot)
    assert bot.request.call_count==1
    assert db.list("notification")[0]["state"]=="UNKNOWN"


def test_purchase_receipt_separates_forecast_and_test_label(db):
    order=prepare(db);payload=render(db,order,"purchased",test=True)
    assert "ТЕСТ" in payload["text"] and "Подарок куплен" in payload["text"]
    assert "Ожидаемая прибыль" in payload["text"] and "+40 ₽" in payload["text"]
    assert "1 TON · 100 ₽" in payload["text"]
