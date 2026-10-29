from fastapi.testclient import TestClient
import pytest
from backend.app.main import create_app

@pytest.fixture
def client(db):
    with TestClient(create_app(db)) as c:yield c

def authenticate(client):
    r=client.post("/api/v1/auth/setup",json={"password":"a-long-test-password"})
    assert r.status_code==200
    return {"X-CSRF-Token":r.json()["csrf"]}

def test_auth_required_and_csrf_enforced(client):
    assert client.get("/api/v1/settings").status_code==401
    headers=authenticate(client)
    assert client.get("/api/v1/settings").status_code==200
    assert client.patch("/api/v1/settings",json={"repricing_enabled":True}).status_code==403
    assert client.patch("/api/v1/settings",headers={**headers,"Origin":"https://evil.example"},json={"repricing_enabled":True}).status_code==403
    assert client.patch("/api/v1/settings",headers=headers,json={"stars_rub":"2"}).status_code==200


def test_order_retry_only_queues_regular_checks_and_deduplicates(db,client):
    headers=authenticate(client)
    db.put("lot","l",{"pair_id":"p","delivery_mode":"telegram_nft"})
    db.put("order","o",{"stage":"AWAITING_ENABLE","lot_id":"l"})
    a=client.post("/api/v1/orders/o/process",headers=headers)
    b=client.post("/api/v1/orders/o/process",headers=headers)
    assert a.status_code==200 and a.json()["task_id"]==b.json()["task_id"]
    assert db.get("order","o")["stage"]=="AWAITING_ENABLE"
    assert db.get("order","o")["allow_low_profit"] is True
    assert db.list("operation")==[] and db.list("ledger")==[]


@pytest.mark.parametrize("state",["UNKNOWN","SUBMITTED","BROWSER_WAIT"])
def test_order_retry_blocks_unresolved_money_operation(db,client,state):
    headers=authenticate(client)
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"stage":"WITHDRAWN","lot_id":"l"})
    db.put("operation","o:transfer",{"state":state,"order_id":"o"})
    assert client.post("/api/v1/orders/o/process",headers=headers).status_code==400


@pytest.mark.parametrize("fields",[{"stage":"COMPLETE"},{"stage":"CANCELLED"},
    {"stage":"PURCHASING"},{"source":"legacy"},{"cancel_requested":True}])
def test_order_retry_respects_holds_and_terminal_stages(db,client,fields):
    headers=authenticate(client)
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"stage":"AWAITING_ENABLE","lot_id":"l",**fields})
    assert client.post("/api/v1/orders/o/process",headers=headers).status_code==400


def test_explicit_retry_releases_manual_hold_before_purchase_and_rebuilds_failed_preview(db,client):
    from backend.app.service import Service
    headers=authenticate(client)
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"stage":"MANUAL","manual_hold":True,"lot_id":"l","gift_slug":"OldSelection-1",
                         "reason":"Закупка не обеспечивает заданную прибыль"})
    db.put("operation","o:buy",{"state":"FAILED","order_id":"o","action":"order.buy","error":"Chrome offline"})
    item=client.get("/api/v1/orders").json()["items"][0]
    assert item["retry_allowed"] is True
    assert client.post("/api/v1/orders/o/process",headers=headers).status_code==200
    assert db.get("order","o")["stage"]=="DISCOVERED" and not db.get("order","o")["manual_hold"]
    assert db.get("operation","o:buy")["state"]=="RETRY_PENDING"
    op=Service(db).make_order_operation("o","buy",{"gift":{"id":"fresh-gift"}},{"purchase_rub":"123"})
    assert op["state"]=="QUEUED" and op["payload"]["gift"]["id"]=="fresh-gift"
    assert op["previous_preflight_error"]=="Chrome offline"
    assert db.list("ledger")==[]


@pytest.mark.parametrize("evidence",["completed","submitted","gift","ledger"])
def test_manual_retry_never_resets_existing_purchase_evidence(db,client,evidence):
    headers=authenticate(client)
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"stage":"MANUAL","manual_hold":True,"lot_id":"l"})
    if evidence=="completed":db.put("operation","o:buy",{"state":"COMPLETE","order_id":"o"})
    elif evidence=="submitted":db.put("operation","o:buy",{"state":"FAILED","submitted_at":1,"order_id":"o"})
    elif evidence=="gift":db.put("order","o",{"mrkt_gift_id":"purchased"})
    else:db.put("ledger","expense",{"order_id":"o","type":"purchase","amount_rub":"100"})
    assert client.get("/api/v1/orders").json()["items"][0]["retry_allowed"] is False
    assert client.post("/api/v1/orders/o/process",headers=headers).status_code==400
    assert db.get("order","o")["manual_hold"] is True

def test_https_origin_is_exact_and_keeps_csrf(db):
    db.config.env["PUBLIC_ORIGIN"]="https://manager.example.com"
    with TestClient(create_app(db)) as c:
        r=c.post("/api/v1/auth/setup",headers={"Origin":"https://manager.example.com"},json={"password":"a-long-test-password"})
        assert r.status_code==200
        assert "Secure" in r.headers["set-cookie"]
        token=r.cookies.get("manager_session")
        headers={"Origin":"https://manager.example.com","Cookie":"manager_session="+token,"X-CSRF-Token":r.json()["csrf"]}
        assert c.patch("/api/v1/settings",headers=headers,json={"stars_rub":"2"}).status_code==200
        assert c.patch("/api/v1/settings",headers={**headers,"Origin":"https://manager.example.com.evil.com"},json={"stars_rub":"2"}).status_code==403
        assert c.patch("/api/v1/settings",headers={**headers,"X-CSRF-Token":"invalid"},json={"stars_rub":"2"}).status_code==403

def test_setup_only_once_and_logout_revokes_session(client):
    headers=authenticate(client)
    assert client.post("/api/v1/auth/setup",json={"password":"another-password"}).status_code==409
    assert client.post("/api/v1/auth/logout",headers=headers).status_code==200
    assert client.get("/api/v1/settings").status_code==401

def test_password_hash_is_not_plaintext(db,client):
    authenticate(client)
    assert "a-long-test-password" not in db.secret("password")
    from backend.app.auth import password_valid
    assert password_valid("a-long-test-password",db.secret("password"))
    assert not password_valid("incorrect",db.secret("password"))

def test_api_does_not_return_credentials(db,client):
    authenticate(client)
    db.secret("playerok_cookies","unique-secret-cookie=value")
    for path in ["/settings","/connections","/dashboard","/events"]:
        r=client.get("/api/v1"+path)
        assert r.status_code==200
        assert "unique-secret-cookie" not in r.text

def test_trade_defaults_are_safe(client):
    authenticate(client);s=client.get("/api/v1/settings").json()
    assert "dry_run" not in s
    assert all(s[k] is False for k in ["repricing_enabled","purchases_enabled","transfers_enabled","promotions_enabled","notifications_enabled","delivery_tested"])
    assert s["daily_purchase_limit_rub"]=="0"


def test_buyer_message_templates_validate_and_start_disabled(client):
    headers=authenticate(client)
    settings=client.get("/api/v1/settings").json()
    assert not settings["buyer_paid_enabled"] and not settings["buyer_delivered_enabled"]
    payload={"buyer_paid_enabled":True,"buyer_paid_template":"Оплата {price} ₽, заказ {order_id}"}
    assert client.patch("/api/v1/settings",headers=headers,json=payload).status_code==200
    invalid=client.patch("/api/v1/settings",headers=headers,json={"buyer_delivered_template":"{recipient.password}"})
    assert invalid.status_code==422
    assert client.get("/api/v1/settings").json()["buyer_paid_template"]==payload["buyer_paid_template"]

def test_price_preview_returns_configuration_error_without_queue(db,client):
    import time
    from backend.app.db import Task
    from sqlalchemy import select
    headers=authenticate(client)
    db.configure(stars_rub="1.26")
    db.put("lot","l",{"pair_id":"p"})
    db.put("quote","p",{"available":True,"ton":"8","fetched_at":time.time()})
    db.put("fx","ton-rub",{"rate":"100","source_at":time.time()})
    r=client.post("/api/v1/pricing/l/preview",headers=headers)
    assert r.status_code==400
    assert "профиль комиссий" in r.json()["detail"]
    with db.sessions() as s:assert s.scalar(select(Task.id).where(Task.kind=="preview_price")) is None
    db.put("fees","default",{"configured":True,"fees":[]})
    r=client.post("/api/v1/pricing/l/preview",headers=headers)
    assert r.status_code==200
    assert r.json()["action"]=="price"
    assert r.json()["state"]=="PREVIEW"
    assert r.json()["payload"]["price"]>800
    assert db.list("ledger")==[]

def test_secret_redaction_in_events(db,client):
    authenticate(client);db.secret("playerok_cookies","cookie=highly-sensitive")
    db.log("error","failure","request failed with cookie=highly-sensitive")
    assert "cookie=highly-sensitive" not in client.get("/api/v1/events").text

def test_pair_uniqueness_and_binding(db,client):
    headers=authenticate(client)
    r=client.post("/api/v1/gift-pairs",headers=headers,json={"collection":"Liberty Figure","model":"Obsidian"})
    r2=client.post("/api/v1/gift-pairs",headers=headers,json={"collection":"liberty figure","model":"obsidian"})
    assert r.json()["id"]==r2.json()["id"]
    db.put("lot","own",{"title":"gift"})
    bound=client.patch("/api/v1/lots/own/binding",headers=headers,json={"pair_id":r.json()["id"]})
    assert bound.status_code==200
    assert bound.json()["pair_id"]==r.json()["id"]
    assert bound.json()["delivery_mode"]=="telegram_nft"
    ordinary=client.patch("/api/v1/lots/own/binding",headers=headers,json={"delivery_mode":"manual"})
    assert ordinary.status_code==200
    assert ordinary.json()["pair_id"] is None
    assert ordinary.json()["delivery_mode"]=="manual"

def test_lots_keep_playerok_order_after_local_edits_and_resync(db,client,monkeypatch):
    from backend.app.service import Service
    from unittest.mock import Mock
    authenticate(client)
    remote=Mock()
    remote.lots.return_value=[{"id":"first","name":"40 ROBUX"},{"id":"second","name":"80 ROBUX"},{"id":"last","name":"Liberty Figure"}]
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:remote)
    svc=Service(db)
    svc.sync_lots()
    ids=lambda:[r["id"] for r in client.get("/api/v1/lots").json()["items"]]
    assert ids()==["first","second","last"]
    db.put("lot","last",{"description":"Refreshed locally","pair_id":"pair"})
    db.put("lot","second",{"price":123})
    assert ids()==["first","second","last"]
    remote.lots.return_value=[{"id":"second"},{"id":"first"}]
    svc.sync_lots()
    assert ids()==["second","first","last"]
    assert db.get("lot","last")["missing"] is True

def test_bulk_promotion_schedules_validate_all_lots_and_preserve_history(db,client):
    headers=authenticate(client)
    for id in ["a","b"]:db.put("lot",id,{"status":"APPROVED"})
    payload={"item_ids":["a","b","a"],"enabled":False,"interval":7200,"max_cost_rub":"25"}
    r=client.put("/api/v1/promotions/schedules/bulk",headers=headers,json=payload)
    assert r.status_code==200 and r.json()["count"]==2
    assert len(db.list("promotion_schedule"))==2
    assert db.get("promotion_schedule","b")["interval"]==7200
    r=client.put("/api/v1/promotions/schedules/bulk",headers=headers,json={**payload,"item_ids":["a","missing"],"interval":3600})
    assert r.status_code==400
    assert db.get("promotion_schedule","a")["interval"]==7200
    db.put("operation","paid",{"action":"promotion","state":"COMPLETE","payload":{"lot_id":"a"}})
    db.put("ledger","cost",{"type":"promotion","lot_id":"a","amount_rub":"25"})
    assert client.delete("/api/v1/promotions/a/schedule").status_code==403
    assert client.delete("/api/v1/promotions/a/schedule",headers=headers).status_code==200
    assert db.get("promotion_schedule","a") is None
    assert db.get("promotion_schedule","b") is not None
    assert db.get("operation","paid")["state"]=="COMPLETE"
    assert db.get("ledger","cost")["amount_rub"]=="25"
    assert db.patch_existing("promotion_schedule","a",{"next_at":123}) is False
    assert db.get("promotion_schedule","a") is None

def test_competitor_external_urls_rejected(db,client):
    headers=authenticate(client)
    db.put("lot","l",{});db.put("pair","p",{})
    r=client.post("/api/v1/competitors",headers=headers,json={"url":"http://127.0.0.1/secret","lot_id":"l","pair_id":"p"})
    assert r.status_code==400

def test_notifications_cannot_conflict_with_old_bot(client,monkeypatch):
    headers=authenticate(client)
    monkeypatch.setattr("backend.app.main.old_running",lambda:True)
    r=client.patch("/api/v1/settings",headers=headers,json={"notifications_enabled":True})
    assert r.status_code==400
    assert client.patch("/api/v1/settings",headers=headers,json={"repricing_enabled":True}).status_code==400

def test_unverified_lot_operations_explicitly_manual(client):
    authenticate(client)
    capabilities=client.get("/api/v1/lots/capabilities").json()
    assert capabilities["quantity"] is False
    assert capabilities["delete"] is False
    assert capabilities["premium"] is True

def test_settings_validation_and_manual_order_protection(db,client):
    headers=authenticate(client)
    assert client.patch("/api/v1/settings",headers=headers,json={"purchase_limit_rub":-1}).status_code==422
    assert client.patch("/api/v1/settings",headers=headers,json={"emergency_stop":None}).status_code==400
    db.put("order","old",{"stage":"MANUAL"})
    assert client.post("/api/v1/orders/old/process",headers=headers).status_code==400

def test_finance_separates_real_from_estimated(db,client):
    authenticate(client)
    db.put("estimate","l",{"profit_rub":"10000"})
    result=client.get("/api/v1/finance").json()
    assert result["profit_rub"]=="0"
    assert len(result["estimates"])==1

def test_creating_listing_requires_exact_pair(db,client):
    headers=authenticate(client)
    png=b"\x89PNG\r\n\x1a\nimage"
    fields={"title":"Gift","collection":"Liberty Figure","price":"100"}
    assert client.post("/api/v1/lots/create-preview",headers=headers,data=fields,files={"image":("gift.png",png,"image/png")}).status_code==422
    db.put("pair","p",{"collection":"Liberty Figure","model":"Obsidian"})
    r=client.post("/api/v1/lots/create-preview",headers=headers,data={**fields,"pair_id":"p"},files={"image":("gift.png",png,"image/png")})
    assert r.status_code==200
    assert r.json()["payload"]["fields"]["pair_id"]=="p"
    assert r.json()["state"]=="PREVIEW"
