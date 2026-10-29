import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock, AsyncMock

import pytest
from telethon import types

from backend.app.service import Service
from backend.app.safety import ManualRequired


@pytest.fixture
def flow(db,monkeypatch):
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True,
                 purchase_limit_rub="1000",daily_purchase_limit_rub="1000")
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"stage":"DISCOVERED","lot_id":"l","sale_rub":"80","recipient":"buyer"})
    fresh={"id":"o","status":"PAID","sale_rub":"80","recipient":"buyer"}
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:NS(orders=lambda:[fresh]))
    svc=Service(db)
    client=NS(get_entity=AsyncMock(return_value=types.User(id=123)))
    monkeypatch.setattr(svc.mrkt,"telegram",lambda op:asyncio.run(op(client)))
    gift={"id":"gift","name":"Gift-5","salePrice":1000000000}
    monkeypatch.setattr(svc,"quote",Mock(return_value={"gift":gift}))
    monkeypatch.setattr(svc,"estimate",Mock(return_value={"profitable":False,"purchase_rub":"100",
                         "quote_ton":"1","transfer_rub":"25","actual_profit_rub":"-45"}))
    monkeypatch.setattr(svc.mrkt,"balances",Mock(return_value={"mrkt_ton":"100","stars":100}))
    return svc,fresh,gift


@pytest.mark.parametrize("forced",[False,True])
def test_only_explicit_order_override_skips_profit_gate(db,flow,forced):
    svc,_,_=flow
    db.put("order","o",{"allow_low_profit":forced})
    if not forced:
        with pytest.raises(ManualRequired,match="прибыль"):svc.process_order("o")
        assert db.get("operation","o:buy") is None
    else:
        svc.process_order("o")
        op=db.get("operation","o:buy")
        assert op["state"]=="QUEUED" and op["expected"]["profit_overridden"] is True
        assert op["expected"]["actual_profit_rub"]=="-45"
    assert db.list("ledger")==[]


@pytest.mark.parametrize("forced",[False,True])
def test_profit_override_also_applies_at_purchase_preflight(db,flow,forced):
    svc,_,gift=flow
    db.put("order","o",{"allow_low_profit":forced,"stage":"PURCHASING"})
    op={"order_id":"o","action":"order.buy","payload":{"gift":gift}}
    if forced:svc.order_preflight(op)
    else:
        with pytest.raises(ManualRequired,match="Прибыль"):svc.order_preflight(op)


def test_forced_order_still_checks_expense_limits_and_payment(db,flow):
    svc,fresh,gift=flow
    db.put("order","o",{"allow_low_profit":True,"stage":"PURCHASING"})
    op={"order_id":"o","action":"order.buy","payload":{"gift":gift}}
    db.configure(purchase_limit_rub="50")
    with pytest.raises(ManualRequired,match="Лимит"):svc.order_preflight(op)
    fresh["status"]="REFUNDED"
    with pytest.raises(ManualRequired,match="Оплата"):svc.order_preflight(op)


@pytest.mark.parametrize("per_order,daily,spent,allowed",[
    ("0","0",100000,True),("0","1000",0,True),("1000","0",100000,True),
    ("50","0",0,False),("0","50",0,False),("0","150",100,False),
    ("100","100",0,True),
])
def test_zero_purchase_limits_are_unlimited_but_positive_limits_apply(db,flow,monkeypatch,per_order,daily,spent,allowed):
    svc,_,gift=flow
    db.configure(purchase_limit_rub=per_order,daily_purchase_limit_rub=daily)
    db.put("order","o",{"allow_low_profit":True,"stage":"PURCHASING"})
    monkeypatch.setattr(svc,"daily_spent",lambda *_:spent)
    op={"order_id":"o","action":"order.buy","payload":{"gift":gift}}
    if allowed:svc.order_preflight(op)
    else:
        with pytest.raises(ManualRequired,match="Лимит"):svc.order_preflight(op)
