import json
import time

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app import catalog_credit as testing
from backend.app.catalog_pricing import price


@pytest.fixture
def owner(db):
    db.put("lot","lot",{"title":"Gift","pair_id":"pair","delivery_mode":"telegram_nft"})
    db.put("pair","pair",{"collection":"Liberty Figure","model":"Obsidian"})
    db.configure(emergency_stop=False,purchases_enabled=True,transfers_enabled=True,delivery_tested=True,ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1.26")
    db.put("fees","default",{"configured":True,"fees":[]})
    with TestClient(create_app(db)) as client:
        assert client.get("/api/v1/catalog-codes").status_code==401
        result=client.post("/api/v1/auth/setup",json={"password":"a-long-test-password"})
        yield client,{"X-CSRF-Token":result.json()["csrf"]}


def make(owner):
    client,headers=owner
    result=client.post("/api/v1/catalog-codes",headers=headers,json={"amount_rub":"1000","recipient":"@buyer","lot_id":"lot"})
    assert result.status_code==200,result.text
    return result.json()


@pytest.mark.parametrize("optional",[{}, {"lot_id":None}, {"lot_id":""}])
def test_code_without_lot_opens_home_and_keeps_usable_balance(db,owner,optional):
    client,headers=owner
    result=client.post("/api/v1/catalog-codes",headers=headers,json={"amount_rub":"1000","recipient":"buyer",**optional})
    assert result.status_code==200,result.text
    code=result.json()
    assert code["url"]=="http://127.0.0.1:3180/#code="+code["code"]
    assert code["collection"]=="" and code["valid"]
    token=testing.activate(db,code["code"],"local")
    assert testing.view(testing.session(db,token))["balance_rub"]=="1000"


def test_code_create_lists_all_statuses_and_protects_management(owner):
    client,headers=owner;code=make(owner)
    assert len(code["code"])==9 and code["balance_rub"]=="1000"
    assert code["url"].endswith("#code="+code["code"])
    listed=client.get("/api/v1/catalog-codes?status=valid")
    assert listed.headers["Cache-Control"]=="no-store"
    assert listed.json()["items"][0]["id"]==code["id"]
    assert "code_encrypted" not in listed.text
    assert client.patch(f"/api/v1/catalog-codes/{code['id']}/balance",json={"balance_rub":"1"}).status_code==403
    assert client.delete(f"/api/v1/catalog-codes/{code['id']}").status_code==403
    changed=client.patch(f"/api/v1/catalog-codes/{code['id']}/balance",headers=headers,json={"balance_rub":"0"})
    assert changed.json()["valid"] is False
    assert client.get("/api/v1/catalog-codes?status=invalid").json()["counts"]=={"all":1,"valid":0,"invalid":1}
    restored=client.patch(f"/api/v1/catalog-codes/{code['id']}/balance",headers=headers,json={"balance_rub":"500"})
    assert restored.json()["valid"] is True


def test_delivered_single_use_code_cannot_be_reopened_and_keeps_history(db,owner):
    client,headers=owner;code=make(owner);payment=db.get("catalog_credit_payment",code["id"])
    testing.choose(db,payment,{"id":"gift","collection":"Gift","model":"Model","number":1,"slug":"Gift-1","price_ton":"1"},price(db,"1")["sale_price"],"buyer")
    from backend.app.service import Service
    testing.fulfill(Service(db),code["id"])
    oid=db.get("catalog_credit_payment",code["id"])["fulfillment_order_id"]
    order=db.get("order",oid)
    testing.delivered(db,{**order,"gift_slug":"Gift-1"})
    changed=client.patch(f"/api/v1/catalog-codes/{code['id']}/balance",headers=headers,json={"balance_rub":"250","note":"Корректировка"})
    assert changed.status_code==400
    closed=db.get("catalog_credit_payment",code["id"])
    assert closed["paid_rub"]=="1000" and closed["state"]=="DELETED"
    assert len(closed["purchases"])==1
    events=client.get(f"/api/v1/catalog-codes/{code['id']}/events").json()["items"]
    assert {e["action"] for e in events}=={"CREATED","FULFILLMENT_QUEUED","FULFILLMENT_STARTED","GIFT_DELIVERED"}
    assert code["code"] not in json.dumps(events)


def test_delete_revokes_code_and_existing_cookie_but_keeps_history(db,owner):
    client,headers=owner;code=make(owner)
    token=testing.activate(db,code["code"],"local")
    result=client.delete(f"/api/v1/catalog-codes/{code['id']}",headers=headers)
    assert result.status_code==200 and not result.json()["valid"]
    with pytest.raises(ValueError):testing.activate(db,code["code"],"other")
    with pytest.raises(ValueError,match="удалён"):testing.session(db,token)
    assert client.patch(f"/api/v1/catalog-codes/{code['id']}/balance",headers=headers,json={"balance_rub":"500"}).status_code==400
    assert client.get("/api/v1/catalog-codes?status=invalid").json()["items"][0]["state"]=="DELETED"
    assert any(e["action"]=="DELETED" for e in client.get(f"/api/v1/catalog-codes/{code['id']}/events").json()["items"])


def test_active_topup_amount_is_not_changed_by_admin(db,owner):
    client,headers=owner;code=make(owner)
    testing.choose(db,db.get("catalog_credit_payment",code["id"]),{"id":"gift","collection":"Gift","model":"Model","number":1,"slug":"Gift-1","price_ton":"20"},price(db,"20")["sale_price"],"buyer")
    url=f"/api/v1/catalog-codes/{code['id']}/balance"
    assert client.patch(url,headers=headers,json={"balance_rub":"2000"}).status_code==400
    assert db.get("catalog_credit_payment",code["id"])["paid_rub"]=="1000"
    record=db.get("catalog_credit_payment",code["id"])
    db.put("catalog_checkout",record["checkout_id"],{"state":"EXPIRED"})
    testing.checkout_result(db,db.get("catalog_checkout",record["checkout_id"]))
    assert client.patch(url,headers=headers,json={"balance_rub":"2000"}).status_code==200


@pytest.mark.parametrize("payload",[{"amount_rub":-1},{"amount_rub":"NaN"},{"recipient":"https://evil.example"},{"amount_rub":"0.001"}])
def test_invalid_input_is_rejected(owner,payload):
    client,headers=owner
    data={"amount_rub":"1000","recipient":"buyer","lot_id":"lot",**payload}
    assert client.post("/api/v1/catalog-codes",headers=headers,json=data).status_code==422
