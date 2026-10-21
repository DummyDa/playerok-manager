from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio,time
import pytest
from telethon import types
from backend.app.integrations.clients import Mrkt,ManualRequired
from backend.app.service import Service
from backend.app.operations import recover

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
