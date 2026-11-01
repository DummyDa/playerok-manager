from io import BytesIO
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from PIL import Image
from backend.app import catalog_checkout as checkout
from backend.app.service import Service
from backend.app.catalog_pricing import price
from backend.app.operations import recover


@pytest.fixture
def prepared(db,monkeypatch):
    db.configure(ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1.26",description_template="Вы получите {collection} #{number}. {gift_link}")
    db.put("fees","default",{"configured":True,"fees":[]})
    gift={"id":"gift","collection":"Chill Flame","model":"Ghost Light","number":5,"slug":"ChillFlame-5","price_ton":"4"}
    raw={"id":"gift","name":"ChillFlame-5","isOnSale":True,"salePrice":4000000000,"nextTransferDate":None}
    service=Service(db);monkeypatch.setattr(service.mrkt,"request",Mock(return_value=raw))
    image=BytesIO();Image.new("RGBA",(300,300),"#3366ff").save(image,"PNG")
    monkeypatch.setattr(checkout.requests,"get",Mock(return_value=NS(content=image.getvalue(),raise_for_status=lambda:None)))
    client=Mock();client.create.return_value={"item_id":"lot","slug":"catalog-lot","status":"APPROVED"}
    client.item.return_value={"id":"lot","status":"APPROVED"}
    client.orders.return_value=[]
    client.client._graphql_post_json.return_value={"removeItem":{"id":"lot"}}
    monkeypatch.setattr(checkout,"Playerok",lambda _:client)
    return service,gift,raw,client


def test_click_creates_one_exact_nft_listing_with_cover_and_dashboard_description(db,prepared):
    service,gift,raw,client=prepared
    shown=price(db,"4")["sale_price"]
    key=checkout.request_checkout(db,gift,shown)
    assert checkout.request_checkout(db,gift,shown)==key
    checkout.create(service,key);checkout.create(service,key)
    client.create.assert_called_once()
    payload,picture=client.create.call_args.args
    assert Image.open(BytesIO(picture)).size==(1280,960)
    assert payload["title"]=="🎁 Chill Flame #5 | Ghost Light | ⚡ АВТОВЫДАЧА"
    assert "https://t.me/nft/ChillFlame-5" in payload["description"]
    assert payload["price"]==shown
    record=db.get("catalog_checkout",key)
    assert record["state"]=="READY" and 899<record["expires_at"]-time.time()<=900
    assert db.get("lot","lot")["catalog_gift_id"]=="gift"


def test_changed_price_does_not_publish(db,prepared):
    service,gift,raw,client=prepared
    key=checkout.request_checkout(db,gift,price(db,"4")["sale_price"])
    raw["salePrice"]=5000000000
    checkout.create(service,key)
    assert db.get("catalog_checkout",key)["state"]=="PRICE_CHANGED"
    assert db.get("catalog_checkout",key)["updated_gift"]["price_ton"]=="5"
    updated=db.get("catalog_checkout",key)
    client.create.assert_not_called()
    checkout.request_checkout(db,updated["updated_gift"],updated["price_rub"])
    checkout.create(service,key)
    client.create.assert_called_once()


def test_published_topup_is_not_repriced_or_recreated(db,prepared):
    from backend.app import funded_orders
    service,gift,raw,client=prepared
    shown=price(db,"4")["sale_price"]
    db.put("grant","original-order",{"state":"PREPARING","checkout_id":"topup","selected":gift,"selected_price":shown,"revision":1})
    db.put("catalog_checkout","topup",{"state":"QUEUED","gift":gift,"shown_price":shown,"funding_grant_id":"original-order","funding_revision":1,"topup_rub":"200"})
    checkout.create(service,"topup")
    assert db.get("grant","original-order")["state"]=="AWAITING_TOPUP"
    published=db.get("catalog_checkout","topup")
    assert published["price_rub"]==200
    raw["salePrice"]=7000000000
    db.configure(manual_ton_rub="200",catalog_profit_value="50")
    checkout.create(service,"topup")
    funded_orders.checkout_result(db,published)
    client.create.assert_called_once()
    assert client.create.call_args.args[0]["price"]==200
    assert db.get("catalog_checkout","topup")==published
    assert db.get("grant","original-order")["selected_price"]==shown


def test_ambiguous_creation_is_not_automatically_repeated(db,prepared):
    service,gift,raw,client=prepared
    key=checkout.request_checkout(db,gift,price(db,"4")["sale_price"])
    client.create.side_effect=TimeoutError("unknown")
    checkout.create(service,key);checkout.create(service,key)
    assert db.get("catalog_checkout",key)["state"]=="UNKNOWN"
    client.create.assert_called_once()


@pytest.mark.parametrize("paid",[False,True])
def test_expiration_removes_only_unpaid_catalog_listing(db,prepared,paid):
    service,gift,raw,client=prepared
    key=checkout.request_checkout(db,gift,price(db,"4")["sale_price"]);checkout.create(service,key)
    db.put("catalog_checkout",key,{"expires_at":time.time()-1})
    if paid:client.orders.return_value=[{"lot_id":"lot","status":"PAID"}]
    checkout.expire(service)
    assert db.get("catalog_checkout",key)["state"]==("PAID" if paid else "EXPIRED")
    assert client.client._graphql_post_json.call_count==(0 if paid else 1)


def test_restart_never_repeats_uncertain_listing_creation(db):
    db.put("catalog_checkout","gift",{"state":"CREATING"})
    recover(db)
    assert db.get("catalog_checkout","gift")["state"]=="UNKNOWN"


def test_exact_order_quote_never_substitutes_another_nft(db,prepared):
    service,gift,raw,client=prepared
    lot={"catalog_gift_id":"gift","catalog_gift_slug":"ChillFlame-5"}
    assert checkout.exact_quote(service,lot)["gift"]["id"]=="gift"
    raw["id"]="another"
    with pytest.raises(RuntimeError,match="подмена"):checkout.exact_quote(service,lot)


def test_rate_limit_before_publication_waits_without_releasing_topup(db,prepared):
    from backend.app import catalog_credit
    from backend.app.safety import RateLimited
    from backend.app.db import Task
    from sqlalchemy import select
    service,gift,raw,client=prepared
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True)
    wallet,_=catalog_credit.create(db,"100","buyer")
    pending=catalog_credit.choose(db,wallet,gift,price(db,"4")["sale_price"],"buyer")
    key=pending["checkout_id"]
    service.mrkt.request.side_effect=RateLimited("MRKT занят",time.time()+30)
    checkout.create(service,key)
    waiting=db.get("catalog_checkout",key)
    assert waiting["state"]=="QUEUED" and waiting["retry_at"]>time.time()+25
    assert db.get("catalog_credit_payment",wallet["id"])["state"]=="PREPARING"
    assert db.get("catalog_credit_payment",wallet["id"])["reservation_rub"]=="100"
    client.create.assert_not_called()
    with db.sessions() as session:
        tasks=list(session.scalars(select(Task).where(Task.dedupe=="catalog_create:"+key+":wait:1")))
        assert len(tasks)==1 and tasks[0].due==waiting["retry_at"]
    checkout.create(service,key)
    assert service.mrkt.request.call_count==1
    db.put("catalog_checkout",key,{"retry_at":0})
    service.mrkt.request.side_effect=None
    checkout.create(service,key)
    assert db.get("catalog_checkout",key)["state"]=="READY"
    client.create.assert_called_once()


def test_rate_limit_after_publication_submission_is_not_retried(db,prepared):
    from backend.app.safety import RateLimited
    service,gift,raw,client=prepared
    key=checkout.request_checkout(db,gift,price(db,"4")["sale_price"])
    client.create.side_effect=RateLimited("Unknown write",time.time()+20)
    checkout.create(service,key);checkout.create(service,key)
    assert db.get("catalog_checkout",key)["state"]=="UNKNOWN"
    client.create.assert_called_once()


def test_price_changed_before_queue_returns_updated_card_without_publication(db,prepared):
    service,gift,raw,client=prepared
    shown=price(db,"4")["sale_price"]
    db.configure(catalog_profit_value="20")
    key=checkout.request_checkout(db,gift,shown)
    changed=db.get("catalog_checkout",key)
    assert changed["state"]=="PRICE_CHANGED"
    assert changed["updated_gift"]["price_rub"]==price(db,"4")["sale_price"]
    checkout.create(service,key)
    client.create.assert_not_called()


def test_person_can_hold_only_five_listings_until_expiration_is_confirmed(db,prepared):
    service,gift,raw,client=prepared
    shown=price(db,"4")["sale_price"]
    owner=("visitor:one","ip:one")
    for index in range(5):checkout.request_checkout(db,{**gift,"id":str(index)},shown,owner)
    assert checkout.request_checkout(db,{**gift,"id":"0"},shown,owner)=="0"
    with pytest.raises(ValueError,match="слишком много лотов"):
        checkout.request_checkout(db,{**gift,"id":"six"},shown,owner)
    db.put("catalog_checkout","0",{"state":"READY","submitted_at":time.time()-901,"expires_at":time.time()-1})
    with pytest.raises(ValueError,match="слишком много лотов"):
        checkout.request_checkout(db,{**gift,"id":"six"},shown,owner)
    db.put("catalog_checkout","0",{"state":"EXPIRED"})
    assert checkout.request_checkout(db,{**gift,"id":"six"},shown,owner)=="six"
    assert checkout.request_checkout(db,{**gift,"id":"other"},shown,("visitor:two","ip:two"))=="other"


def test_cookie_reset_does_not_bypass_ip_quota_and_paid_slot_keeps_fifteen_minutes(db,prepared):
    service,gift,raw,client=prepared
    shown=price(db,"4")["sale_price"]
    for index in range(5):
        checkout.request_checkout(db,{**gift,"id":str(index)},shown,("visitor:"+str(index),"ip:one"))
    db.put("catalog_checkout","0",{"state":"PAID","submitted_at":time.time(),"expires_at":time.time()+900})
    with pytest.raises(ValueError,match="слишком много лотов"):
        checkout.request_checkout(db,{**gift,"id":"six"},shown,("visitor:new","ip:one"))
    db.put("catalog_checkout","0",{"expires_at":time.time()-1})
    assert checkout.request_checkout(db,{**gift,"id":"six"},shown,("visitor:new","ip:one"))=="six"


def test_concurrent_requests_cannot_take_sixth_personal_slot(db,prepared):
    from concurrent.futures import ThreadPoolExecutor
    _,gift,_,_=prepared
    shown=price(db,"4")["sale_price"]
    def attempt(index):
        try:checkout.request_checkout(db,{**gift,"id":str(index)},shown,("visitor:one",));return True
        except ValueError as exc:assert "слишком много лотов" in str(exc);return False
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(attempt,range(6)))
    assert sum(results)==5
