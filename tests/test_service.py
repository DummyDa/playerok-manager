import time
import pytest
from unittest.mock import Mock
from backend.app.service import Service
from backend.app.pricing import PricingError
from backend.app.db import Task
from sqlalchemy import select

def prepare(db):
    db.configure(stars_rub="2")
    db.put("fees","default",{"configured":True,"fees":[]})
    db.put("pair","p",{"collection":"Liberty Figure","model":"Obsidian"})
    db.put("lot","l",{"status":"APPROVED","pair_id":"p","price":1500})
    db.put("quote","p",{"available":True,"ton":"1","fetched_at":time.time()})
    db.put("fx","ton-rub",{"rate":"100","source_at":time.time()})

def test_stale_fx_and_quote_stop_pricing(db):
    prepare(db);svc=Service(db)
    assert svc.estimate("l")["sale_price"]==155
    db.put("fx","ton-rub",{"source_at":time.time()-1000})
    with pytest.raises(PricingError,match="Курс"):svc.estimate("l")
    db.put("fx","ton-rub",{"source_at":time.time()})
    db.put("quote","p",{"fetched_at":time.time()-10000})
    with pytest.raises(PricingError,match="устарела"):svc.estimate("l")

def test_pairs_are_queried_once_for_multiple_lots(db,monkeypatch):
    prepare(db)
    for i in range(10):db.put("lot",str(i),{"status":"APPROVED","pair_id":"p"})
    svc=Service(db);quote=Mock();monkeypatch.setattr(svc,"quote",quote)
    svc.quotes();quote.assert_called_once_with("p")

def test_missing_model_does_not_pause_listing(db,monkeypatch):
    prepare(db);svc=Service(db);monkeypatch.setattr(svc.mrkt,"search",lambda *_:[])
    assert svc.quote("p",True) is None
    assert db.get("lot","l")["status"]=="APPROVED"
    assert db.get("quote","p")["available"] is False

def test_disabled_delivery_never_buys(db,monkeypatch):
    db.put("lot","l",{"delivery_mode":"telegram_nft","pair_id":"p"})
    db.put("order","o",{"stage":"DISCOVERED","lot_id":"l"})
    svc=Service(db);buy=Mock();monkeypatch.setattr(svc.mrkt,"buy",buy)
    svc.process_order("o")
    buy.assert_not_called();assert db.get("order","o")["stage"]=="AWAITING_ENABLE"

def test_imported_manual_order_cannot_auto_resume(db):
    db.put("order","o",{"stage":"MANUAL","source":"legacy"})
    Service(db).process_order("o")
    assert db.get("order","o")["stage"]=="MANUAL"

def test_nano_price_normalization_and_exact_model_filter(db,monkeypatch):
    svc=Service(db)
    monkeypatch.setattr(svc.mrkt,"request",lambda *args:{"gifts":[
        {"id":"wrong","collectionName":"Liberty Figure","modelName":"Gold","salePrice":1},
        {"id":"expensive","collectionName":"Liberty Figure","modelName":"Obsidian","salePrice":2000000000},
        {"id":"cheap","collectionName":"Liberty Figure","modelName":"Obsidian","salePrice":1000000000}]})
    assert svc.mrkt.search("Liberty Figure","Obsidian")[0]["id"]=="cheap"
    assert svc.mrkt.ton(1000000000)=="1"

def test_order_first_sync_is_historical(db,monkeypatch):
    from backend.app import service
    db.put("lot","l",{"delivery_mode":"telegram_nft","pair_id":"p"})
    fake=Mock();fake.orders.return_value=[{"id":"old","status":"PAID","lot_id":"l","sale_rub":"100","title":"gift","recipient":"buyer"}]
    monkeypatch.setattr(service,"Playerok",lambda _:fake)
    Service(db).orders()
    assert db.get("order","old")["stage"]=="HISTORICAL"
    fake.orders.return_value.append({"id":"new","status":"PAID","lot_id":"l","sale_rub":"100","title":"gift","recipient":"buyer"})
    Service(db).orders()
    assert db.get("order","new")["stage"]=="DISCOVERED"
