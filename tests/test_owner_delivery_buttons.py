from unittest.mock import Mock
from types import SimpleNamespace
from sqlalchemy import select
from backend.app.db import Task
from backend.app.owner_notifications import render
from backend.app.owner_delivery_buttons import handle


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
