import time
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.service import Service
from backend.app.pricing import D,PricingError,PremiumQuoteRequired

def prepare(db):
    db.configure(stars_rub="2")
    db.put("fees","default",{"configured":True,"fees":[]})
    db.put("pair","p",{"collection":"Example Gift","model":"Model"})
    db.put("lot","l",{"status":"APPROVED","pair_id":"p","price":200})
    db.put("quote","p",{"available":True,"ton":"1","fetched_at":time.time()})
    db.put("fx","ton-rub",{"rate":"100","source_at":time.time()})


def test_paid_margin_is_available_even_when_target_price_exceeds_listing_ceiling(db):
    prepare(db)
    db.put("strategy","default",{"profit_value":50,"max_price":160})
    service=Service(db)
    with pytest.raises(PricingError,match="максимум"):service.estimate("l")
    result=service.estimate("l",sale=151)
    assert D(result["actual_profit_rub"])==1
    assert result["profitable"] is False  # below listing target, still nonnegative


def test_preview_refreshes_stale_fx_in_worker_and_api_only_queues(db,monkeypatch):
    prepare(db);db.put("fx","ton-rub",{"source_at":0})
    refresh=Mock(side_effect=lambda self:db.put("fx","ton-rub",{"rate":"80","source_at":time.time()}))
    monkeypatch.setattr(Service,"refresh_fx",lambda self:refresh(self))
    with TestClient(create_app(db)) as api:
        login=api.post("/api/v1/auth/setup",json={"password":"fx-refresh-password"})
        api.headers["X-CSRF-Token"]=login.json()["csrf"]
        result=api.post("/api/v1/pricing/l/preview").json()
        assert result["task_id"]
        refresh.assert_not_called()
        from backend.app.worker import Worker
        op=Worker(db).dispatch("preview_price",{"lot_id":"l"})
        assert D(op["expected"]["ton_rub"])==80
        assert not op["expected"]["fx_stale"]
        refresh.assert_called_once()


@pytest.mark.parametrize("failed",[True,False])
def test_failed_fx_refresh_allows_only_labelled_stale_preview(db,monkeypatch,failed):
    from backend.app.pricing import FxQuoteRequired
    prepare(db);db.put("fx","ton-rub",{"source_at":0})
    service=Service(db)
    refresh=Mock(side_effect=RuntimeError("provider down") if failed else None)
    monkeypatch.setattr(service,"refresh_fx",refresh)
    op=service.preview_price("l")
    assert op["expected"]["fx_stale"] and op["expected"]["fx_warning"]
    assert D(op["expected"]["ton_rub"])==100
    with pytest.raises(FxQuoteRequired):service.estimate("l")
    assert db.list("ledger")==[]


def test_no_saved_fx_does_not_invent_fallback_rate(db,monkeypatch):
    prepare(db)
    monkeypatch.setattr("backend.app.service.effective_fx",lambda db:None)
    service=Service(db);monkeypatch.setattr(service,"refresh_fx",Mock(side_effect=RuntimeError("down")))
    with pytest.raises(PricingError,match="Нет сохранённого курса"):service.preview_price("l")


def test_manual_ton_rate_applies_to_purchase_ton_fees_and_paid_profit(db):
    prepare(db)
    db.configure(ton_rate_mode="manual",manual_ton_rub="80")
    db.put("fx","ton-rub",{"rate":"100","source_at":0})
    db.put("fees","default",{"configured":True,"fees":[
        {"type":"percent","value":"10","basis":"sale"},
        {"type":"ton","value":"0.5"}]})
    result=Service(db).estimate("l",sale=200)
    assert Service(db).estimate("l")["fx_at"]==result["fx_at"]
    assert D(result["purchase_rub"])==80
    assert D(result["ton_rub"])==80
    assert D(result["actual_net_rub"])==140  # 200 - 20 - .5 * 80
    assert D(result["actual_profit_rub"])==10  # net - purchase 80 - Stars 50
    Service(db).refresh_fx()  # no network and no overwrite in manual mode
    assert db.get("fx","ton-rub")["rate"]=="100"
    db.configure(ton_rate_mode="auto")
    with pytest.raises(PricingError,match="устарел"):Service(db).estimate("l")


def premium_lot(db,monkeypatch):
    prepare(db);db.put("lot","l",{"premium":True,"price":200})
    client=Mock();client.premium.side_effect=lambda lot,price:{"id":"premium-"+str(price),"price":str(D(price)/10)}
    monkeypatch.setattr("backend.app.service.Playerok",lambda db:client)
    return client


def test_manually_planned_premium_is_counted_before_purchase_and_survives_sync(db,monkeypatch):
    client=premium_lot(db,monkeypatch)
    db.put("lot","l",{"premium":False,"premium_planned":True})
    db.put("strategy","default",{"premium_mode":"exclude"})
    service=Service(db)
    planned=service.estimate("l",sale=200)
    assert planned["premium_included"] and D(planned["paid_promotion_rub"])==20
    assert D(planned["actual_profit_rub"])==30
    client.lots.return_value=[{"id":"l","name":"Gift","price":200,"status":"APPROVED","premium":False}]
    service.sync_lots()
    assert db.get("lot","l")["premium_planned"] is True
    assert not db.get("lot","l")["premium"]
    assert db.list("ledger")==[] and db.list("operation")==[]
    db.put("lot","l",{"premium_planned":False})
    assert not service.estimate("l")["premium_included"]

def test_premium_auto_detection_cache_and_exact_tariff_for_both_prices(db,monkeypatch):
    client=premium_lot(db,monkeypatch);service=Service(db)
    result=service.estimate("l")
    assert result["premium_included"] and result["premium_count"]==1
    assert D(result["promotion_rub"])==D(result["sale_price"])/10
    assert result["premium_quote_price"]==result["sale_price"]
    assert D(result["current_promotion_rub"])==20
    assert D(result["current_profit_rub"])==30  # 200 - NFT 100 - Stars 50 - premium 20
    called=client.premium.call_count
    service.estimate("l",allow_network=False)
    assert client.premium.call_count==called
    assert {args[0] for args,_ in client.premium.call_args_list}=={"l"}

def test_multiple_promotions_paid_sale_and_profile_fees_remain_separate(db,monkeypatch):
    premium_lot(db,monkeypatch);db.put("strategy","default",{"premium_purchases_per_sale":2})
    result=Service(db).estimate("l",sale=200)
    assert D(result["paid_promotion_rub"])==40
    assert D(result["actual_profit_rub"])==10 and result["profitable"]
    result=Service(db).estimate("l",sale=190)
    assert D(result["actual_profit_rub"])==2 and not result["profitable"]
    assert db.list("ledger")==[]

def test_exclude_and_enabled_schedule_and_stale_or_missing_tariff(db,monkeypatch):
    client=premium_lot(db,monkeypatch)
    db.put("strategy","default",{"premium_mode":"exclude"})
    assert D(Service(db).estimate("l")["promotion_rub"])==0
    client.premium.assert_not_called()
    db.put("lot","l",{"premium":False});db.put("strategy","default",{"premium_mode":"auto"})
    db.put("promotion_schedule","l",{"enabled":True})
    with pytest.raises(PremiumQuoteRequired):Service(db).estimate("l",allow_network=False)
    result=Service(db).estimate("l")
    record=db.get("premium_quotes","l");prices=record["prices"]
    for row in prices.values():row["checked_at"]=time.time()-901
    db.put("premium_quotes","l",{"prices":prices})
    with pytest.raises(PremiumQuoteRequired):Service(db).estimate("l",allow_network=False)
    client.premium.side_effect=RuntimeError("private-provider-token")
    with pytest.raises(PricingError,match="тариф премиума") as e:Service(db).estimate("l")
    assert "private-provider-token" not in str(e.value)

def test_api_queues_premium_tariff_reads_and_never_accesses_playerok_in_request(db,monkeypatch):
    client=premium_lot(db,monkeypatch)
    with TestClient(create_app(db)) as api:
        login=api.post("/api/v1/auth/setup",json={"password":"premium-profit-password"})
        api.headers["X-CSRF-Token"]=login.json()["csrf"]
        result=api.post("/api/v1/pricing/l/preview").json()
        assert result["task_id"]
        client.premium.assert_not_called()
        from backend.app.worker import Worker
        operation=Worker(db).dispatch("preview_price",{"lot_id":"l"})
        assert operation["expected"]["premium_included"]
        cached=api.post("/api/v1/pricing/l/preview").json()
        assert cached["state"]=="PREVIEW" and D(cached["expected"]["promotion_rub"])>0
        assert db.list("ledger")==[]

def test_finance_and_overview_deduct_actual_premium_once_not_forecast_reserve(db,monkeypatch):
    premium_lot(db,monkeypatch);service=Service(db)
    service.preview_price("l")
    for kind,amount in [("revenue",200),("purchase",100),("transfer",50),("promotion",20)]:service.ledger(kind,kind,amount,"l")
    with TestClient(create_app(db)) as api:
        api.post("/api/v1/auth/setup",json={"password":"premium-finance-password"})
        assert D(api.get("/api/v1/finance").json()["profit_rub"])==30
        assert D(api.get("/api/v1/dashboard").json()["profit_rub"])==30

def test_ordinary_products_never_request_premium_for_mrkt_profit(db,monkeypatch):
    client=premium_lot(db,monkeypatch);db.put("lot","l",{"delivery_mode":"manual"})
    with pytest.raises(PricingError,match="только для Telegram NFT"):Service(db).estimate("l")
    client.premium.assert_not_called()

def test_changed_premium_tariff_rejects_execution_before_listing_mutation(db,monkeypatch):
    from backend.app.operations import submit
    client=premium_lot(db,monkeypatch);service=Service(db)
    operation=service.preview_price("l")
    submit(db,operation["id"])
    client.premium.side_effect=lambda lot,price:{"id":"new-tariff","price":"50"}
    service.execute(operation["id"])
    saved=db.get("operation",operation["id"])
    assert saved["state"]=="FAILED" and "Расчёт изменился" in saved["error"]
    client.update.assert_not_called()
    assert db.list("ledger")==[]

def test_install_invalidates_only_forecasts_and_queues_read_only_refresh_once(db):
    from sqlalchemy import select
    from backend.app.db import Entity,Task
    def refresh_count():
        with db.sessions() as session:
            return len(list(session.scalars(select(Task).where(Task.kind=="reprice_preview"))))
    db.put("estimate","l",{"status":"READY","profit_rub":"999"})
    db.put("order","o",{"stage":"COMPLETE","snapshot":{"profit_rub":"20"}})
    db.put("ledger","p",{"type":"promotion","amount_rub":"10"})
    with db.tx() as session:
        session.delete(session.get(Entity,("schema_feature","premium_pricing")))
    db.initialize()
    assert db.get("estimate","l")["status"]=="STALE"
    assert db.get("order","o")["snapshot"]=={"profit_rub":"20"}
    assert db.get("ledger","p")["amount_rub"]=="10"
    assert refresh_count()==1
    db.initialize()
    assert refresh_count()==1
