from unittest.mock import Mock
from types import SimpleNamespace
from sqlalchemy import select
from backend.app.db import Task
from backend.app.owner_notifications import render
from backend.app.owner_delivery_buttons import handle
from backend.app.owner_delivery_buttons import handle_reply


def prepare(db):
    db.configure(manual_gift_delivery=True,owner_chat_id=77)
    db.put("lot","l",{"pair_id":"p"})
    order=db.put("order","order",{"stage":"WITHDRAWN","lot_id":"l","gift_slug":"Gift-1","mrkt_gift_id":"gift","recipient":"buyer","recipient_id":123,"snapshot":{"transfer_rub":"25"}})
    payload=render(db,order,"purchased")
    db.put("notification","owner:order:purchased",{"state":"SENT","message_id":100,"payload":payload})
    data=payload["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
    callback={"id":"callback","from":{"id":77},"message":{"message_id":100,"chat":{"id":77,"type":"private"}},"data":data}
    return callback


def test_owner_button_requires_confirmation_and_queues_once(db):
    callback=prepare(db);bot=Mock();service=SimpleNamespace(db=db)
    handle(service,bot,callback)
    with db.sessions() as s:assert not list(s.scalars(select(Task)))
    callback["data"]=callback["data"].replace(":ask",":yes")
    handle(service,bot,callback);handle(service,bot,callback)
    with db.sessions() as s:
        tasks=list(s.scalars(select(Task)))
        assert len(tasks)==1 and tasks[0].kind=="manual_delivery_confirm"
    assert not db.get("order","order").get("manual_delivery_receipt")


def test_non_owner_and_unconfirmed_click_cannot_complete_order(db):
    callback=prepare(db);bot=Mock();service=SimpleNamespace(db=db)
    callback["from"]["id"]=88
    handle(service,bot,callback)
    callback["from"]["id"]=77;callback["data"]=callback["data"].replace(":ask",":yes")
    handle(service,bot,callback)
    with db.sessions() as s:assert not list(s.scalars(select(Task)))


def review(db,known_cost=True):
    callback=prepare(db)
    db.configure(manual_gift_delivery=False)
    db.put("order","order",{"stage":"MANUAL","catalog_credit_id":"c","catalog_revision":2})
    db.put("catalog_credit_payment","c",{"state":"REVIEW","selected":{"slug":"Gift-1"},"recipient":"buyer","revision":2,"fulfillment_order_id":"order"})
    db.put("operation","order:transfer",{"order_id":"order","action":"order.transfer","state":"UNKNOWN","submitted_at":1})
    if known_cost:db.put("ledger","purchase",{"order_id":"order","type":"purchase","amount_rub":"100"})
    return callback


def test_review_button_uses_dashboard_settlement_without_rebuy(db):
    callback=review(db);bot=Mock();service=SimpleNamespace(db=db)
    handle(service,bot,callback)
    callback["data"]=callback["data"].replace(":ask",":yes")
    handle(service,bot,callback);handle(service,bot,callback)
    assert db.get("order","order")["owner_delivery_requested"]
    assert db.get("operation","order:transfer")["state"]=="UNKNOWN"
    with db.sessions() as s:
        tasks=list(s.scalars(select(Task)))
        assert len(tasks)==1 and tasks[0].kind=="manual_review_complete"
    assert not db.get("order","order").get("manual_delivery_receipt")


def test_review_confirmation_rejects_changed_gift(db):
    callback=review(db);bot=Mock();service=SimpleNamespace(db=db)
    handle(service,bot,callback)
    db.put("catalog_credit_payment","c",{"revision":3})
    callback["data"]=callback["data"].replace(":ask",":yes")
    handle(service,bot,callback)
    assert not db.get("order","order").get("owner_delivery_requested")


def test_review_submitted_request_cannot_be_closed(db):
    callback=review(db);bot=Mock();service=SimpleNamespace(db=db)
    db.put("operation","order:transfer",{"state":"SUBMITTED"})
    handle(service,bot,callback)
    callback["data"]=callback["data"].replace(":ask",":yes")
    handle(service,bot,callback)
    assert not db.get("order","order").get("owner_delivery_requested")


def test_manual_purchase_cost_reply_is_owner_only_and_bound_to_gift(db):
    callback=review(db,known_cost=False);bot=Mock();bot.request.return_value={"message_id":200};service=SimpleNamespace(db=db)
    handle(service,bot,callback)
    callback["data"]=callback["data"].replace(":ask",":yes")
    handle(service,bot,callback)
    assert not db.get("order","order").get("owner_delivery_requested")
    message={"from":{"id":88},"chat":{"id":77,"type":"private"},"reply_to_message":{"message_id":200},"text":"508,57"}
    assert not handle_reply(service,bot,message)
    message["from"]["id"]=77
    message["text"]="-1"
    assert handle_reply(service,bot,message)
    assert not db.get("order","order").get("owner_purchase_rub")
    message["text"]="508,57"
    assert handle_reply(service,bot,message)
    assert db.get("order","order")["owner_purchase_rub"]=="508.57"
    assert not handle_reply(service,bot,message)
