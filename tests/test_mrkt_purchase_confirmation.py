from unittest.mock import Mock

import pytest

from backend.app.integrations.clients import Mrkt, ManualRequired
from backend.app.service import Service


def gift(**fields):
    return {"id":"gift-id","name":"ChillFlame-306647","salePrice":4182000000,"nextTransferDate":None,**fields}


@pytest.mark.parametrize("response", [True,[],["gift-id"],{"ids":["gift-id"]},{"purchasedGifts":[{"id":"gift-id"}]}])
def test_buy_sends_once_then_verifies_exact_ownership(db,monkeypatch,response):
    db.configure(purchases_enabled=True)
    mrkt=Mrkt(db)
    owned=gift(isMine=True,isOnPlatform=True,isOnSale=False)
    request=Mock(side_effect=[response,owned])
    monkeypatch.setattr(mrkt,"request",request)
    assert mrkt.buy(gift())==owned
    assert [c.args[:2] for c in request.call_args_list]==[("POST","/gifts/buy"),("GET","/gifts/gift/gift-id")]


@pytest.mark.parametrize("fields",[{"isMine":False},{"isOnPlatform":False},{"id":"different"},{"name":"Different-1"}])
def test_response_alone_does_not_prove_purchase(db,monkeypatch,fields):
    db.configure(purchases_enabled=True)
    mrkt=Mrkt(db)
    request=Mock(side_effect=[{"gifts":[gift()]},gift(**{"isMine":True,"isOnPlatform":True,**fields})])
    monkeypatch.setattr(mrkt,"request",request)
    with pytest.raises(ManualRequired):mrkt.buy(gift())
    assert request.call_count==2


@pytest.mark.parametrize("held",[False,True])
def test_reconcile_purchase_restores_checkpoint_without_rebuy(db,monkeypatch,held):
    db.put("order","o",{"lot_id":"l","stage":"MANUAL","manual_hold":held})
    db.put("operation","o:buy",{"action":"order.buy","order_id":"o","state":"UNKNOWN",
        "payload":{"gift":gift()},"expected":{"purchase_rub":"410.58876"}})
    svc=Service(db)
    owned=Mock(return_value=gift(isMine=True,isOnPlatform=True))
    buy=Mock(side_effect=AssertionError("Do not buy again"))
    monkeypatch.setattr(svc.mrkt,"owned_gift",owned)
    monkeypatch.setattr(svc.mrkt,"buy",buy)
    svc.reconcile("o:buy")
    assert db.get("operation","o:buy")["state"]=="COMPLETE"
    assert db.get("order","o")["mrkt_gift_id"]=="gift-id"
    assert db.get("order","o")["stage"]==("MANUAL" if held else "PURCHASED")
    assert len(db.list("ledger"))==1
    buy.assert_not_called()
    # Recovery after an interruption must not duplicate the expense.
    db.put("operation","o:buy",{"state":"UNKNOWN"})
    svc.reconcile("o:buy")
    assert len(db.list("ledger"))==1
