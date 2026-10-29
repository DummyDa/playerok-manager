from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio,time
from datetime import datetime, timezone
import pytest
from telethon import types
from backend.app.integrations.clients import Mrkt,ManualRequired
from backend.app.service import Service
from backend.app.operations import recover


@pytest.mark.parametrize("change", [None,"slug","recipient","incoming","old","refunded","ordinary"])
def test_transfer_receipt_requires_exact_recent_outgoing_gift(db, monkeypatch, change):
    now = 2000.8
    action = types.MessageActionStarGiftUnique(gift=SimpleNamespace(slug="Gift-5"))
    message = types.MessageService(id=100, peer_id=types.PeerUser(123), out=True,
                                  date=datetime.fromtimestamp(2000, timezone.utc), action=action)
    if change == "slug": action.gift.slug = "Other-5"
    elif change == "recipient": message.peer_id = types.PeerUser(999)
    elif change == "incoming": message.out = False
    elif change == "old": message.date = datetime.fromtimestamp(1999, timezone.utc)
    elif change == "refunded": action.refunded = True
    elif change == "ordinary": message = types.Message(id=100,peer_id=types.PeerUser(123),out=True,
                                                       date=message.date,message="Gift-5")
    async def messages(*args, **kwargs):
        assert kwargs == {"limit":100,"min_id":50}
        yield message
    client = SimpleNamespace(get_entity=AsyncMock(return_value=types.User(id=123)), iter_messages=messages)
    mrkt = Mrkt(db)
    monkeypatch.setattr(mrkt, "telegram", lambda op: asyncio.run(op(client)))
    result = mrkt.transfer_receipt("Gift-5", "buyer",123,now,50)
    if change is None:
        assert result == {"message_id":100,"recipient_id":123,"slug":"Gift-5","date":2000.0}
    else:
        assert result is None


def test_transfer_receipt_rejects_changed_username_owner(db, monkeypatch):
    mrkt = Mrkt(db)
    client = SimpleNamespace(get_entity=AsyncMock(return_value=types.User(id=999)))
    monkeypatch.setattr(mrkt, "telegram", lambda op: asyncio.run(op(client)))
    with pytest.raises(ManualRequired, match="ID"):
        mrkt.transfer_receipt("Gift-5","buyer",123,time.time(),50)


def test_hidden_owner_order_uses_matching_service_receipt_before_marking_sent(db,monkeypatch):
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True)
    db.put("lot","l",{"pair_id":"p"})
    order=db.put("order","o",{"stage":"TRANSFERRED","lot_id":"l","gift_slug":"Gift-5",
        "recipient":"buyer","recipient_id":123,"snapshot":{}})
    db.put("operation","o:transfer",{"state":"COMPLETE","submitted_at":2000.8,"payload":{"saved":{"msg_id":50}}})
    svc=Service(db)
    monkeypatch.setattr(svc.mrkt,"owner",lambda _:None)
    receipt=Mock(return_value={"message_id":100,"slug":"Gift-5","recipient_id":123})
    monkeypatch.setattr(svc.mrkt,"transfer_receipt",receipt)
    svc.process_order("o")
    assert db.get("operation","o:mark_sent")["state"]=="QUEUED"
    receipt.assert_called_once_with("Gift-5","buyer",123,2000.8,50)
    assert db.get("order","o")["transfer_verification"]=="telegram_service_message"
    svc.order_preflight({"action":"order.mark_sent","order_id":"o"})

def test_recipient_change_blocks_before_payment_or_transfer(db,monkeypatch):
    db.configure(transfers_enabled=True)
    c=Mrkt(db)
    telegram=SimpleNamespace(get_entity=AsyncMock(return_value=object()),get_input_entity=AsyncMock(return_value=types.InputPeerUser(user_id=999,access_hash=1)))
    def run(operation):return asyncio.run(operation(telegram))
    monkeypatch.setattr(c,"telegram",run)
    with pytest.raises(ManualRequired,match="ID"):
        c.transfer({"msg_id":10,"transfer_stars":25},"recipient",expected_recipient_id=123)

def test_paid_and_free_order_operations_never_rebuy_on_restart(db):
    for action in ["buy","withdraw","transfer","mark_sent"]:
        id="o:"+action
        db.put("operation",id,{"order_id":"o","action":"order."+action,"state":"SUBMITTED","payload":{}})
    db.put("order","o",{"stage":"PURCHASING"})
    recover(db)
    for op in db.list("operation"):assert op["state"]=="UNKNOWN"
    assert db.get("order","o")["stage"]=="MANUAL"

def test_fresh_order_recipient_can_resume_waiting_data(db,monkeypatch):
    from backend.app import service
    db.configure(orders_baselined=True)
    db.put("lot","l",{"delivery_mode":"telegram_nft","pair_id":"p"})
    db.put("order","o",{"stage":"WAITING_DATA","lot_id":"l"})
    fake=SimpleNamespace(orders=lambda:[{"id":"o","status":"PAID","lot_id":"l","title":"gift","sale_rub":"100","recipient":"buyer"}])
    monkeypatch.setattr(service,"Playerok",lambda _:fake)
    Service(db).orders()
    assert db.get("order","o")["stage"]=="DISCOVERED"
    assert db.get("order","o")["recipient"]=="buyer"

def test_finance_pair_snapshot_survives_lot_rebinding(db):
    db.put("pair","old",{"collection":"A","model":"B"});db.put("lot","l",{"pair_id":"old"})
    Service(db).ledger("op","purchase",100,"l")
    db.put("lot","l",{"pair_id":"new"})
    assert db.list("ledger")[0]["pair_id"]=="old"

def test_manual_hold_survives_completion_of_an_inflight_purchase(db):
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True)
    db.put("order","o",{"stage":"PURCHASED","manual_hold":True,"mrkt_gift_id":"g"})
    Service(db).process_order("o")
    assert db.get("order","o")["stage"]=="MANUAL"
    assert db.get("order","o")["mrkt_gift_id"]=="g"

@pytest.mark.parametrize("stage",["DISCOVERED","AWAITING_ENABLE","WAITING_DATA","CHECK_PRICE","PURCHASED","WITHDRAWN","TRANSFERRED"])
def test_regular_product_never_touches_mrkt_even_with_automation_enabled(db,monkeypatch,stage):
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True)
    # Explicit manual mode takes precedence over any old or mistaken pair binding.
    db.put("lot","robux",{"delivery_mode":"manual","pair_id":"old-pair"})
    db.put("order","o",{"stage":stage,"lot_id":"robux","recipient":"buyer"})
    svc=Service(db);svc.mrkt=Mock()
    playerok=Mock(side_effect=AssertionError("Ordinary product must not enter NFT preflight"))
    monkeypatch.setattr("backend.app.service.Playerok",playerok)
    svc.process_order("o")
    assert db.get("order","o")["stage"]=="MANUAL"
    assert "Обычный товар" in db.get("order","o")["reason"]
    assert svc.mrkt.mock_calls==[]
    playerok.assert_not_called()

@pytest.mark.parametrize("action",["buy","withdraw","transfer","mark_sent"])
def test_queued_nft_operations_reject_regular_product(db,action):
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True)
    db.put("lot","l",{"title":"Regular product"})
    db.put("order","o",{"stage":"PURCHASING","lot_id":"l"})
    svc=Service(db);svc.mrkt=Mock()
    op={"order_id":"o","action":"order."+action,"payload":{}}
    with pytest.raises(ManualRequired,match="Обычный товар"):svc.order_preflight(op)
    with pytest.raises(ManualRequired,match="Обычный товар"):svc.execute_order_operation(op)
    assert svc.mrkt.mock_calls==[]

def test_regular_orders_remain_visible_but_are_not_queued_for_delivery(db,monkeypatch):
    from sqlalchemy import select
    from backend.app.db import Task
    db.configure(orders_baselined=True)
    db.put("lot","l",{"status":"APPROVED","title":"300 ROBUX"})
    records=[{"id":"new","status":"PAID","lot_id":"l","title":"300 ROBUX","sale_rub":"300"}]
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:SimpleNamespace(orders=lambda:records))
    Service(db).orders()
    assert db.get("order","new")["stage"]=="MANUAL"
    assert db.get("order","new")["sale_rub"]=="300"
    with db.sessions() as s:assert s.scalar(select(Task.id).where(Task.kind=="process_order")) is None


def test_chrome_offline_waits_before_creating_purchase(db):
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True,delivery_transport="chrome_extension")
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"stage":"DISCOVERED","lot_id":"l"})
    Service(db).process_order("o")
    assert db.get("order","o")["stage"]=="AWAITING_ENABLE"
    assert db.get("operation","o:buy") is None


@pytest.mark.parametrize("state,submitted", [("FAILED",False),("FAILED",True),("UNKNOWN",False)])
def test_only_chrome_readiness_failure_before_submission_can_requeue(db,state,submitted):
    op={"state":state,"error":"Расширение Chrome не готово: откройте Chrome и войдите в Telegram Web A"}
    if submitted:op["submitted_at"]=time.time()
    db.put("operation","o:buy",op)
    svc=Service(db)
    if state=="FAILED" and not submitted:
        assert svc.make_order_operation("o","buy",{"gift":{"id":"fresh"}},{} )["state"]=="QUEUED"
    else:
        with pytest.raises(ManualRequired):svc.make_order_operation("o","buy",{}, {})
