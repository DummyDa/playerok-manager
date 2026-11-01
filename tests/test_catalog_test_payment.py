from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from backend.app import catalog_test_payment as testing
from backend.app.catalog_pricing import price


@pytest.fixture
def payment(db):
    db.configure(ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1.26")
    db.put("fees","default",{"configured":True,"fees":[]})
    db.put("pair","pair",{"collection":"Liberty Figure","model":"Obsidian"})
    db.put("lot","lot",{"pair_id":"pair","price":800})
    return testing.create(db,"800","buyer","lot")


def test_code_and_cookie_restore_after_eight_hours(db,payment,monkeypatch):
    original,code=payment
    monkeypatch.setattr(testing.time,"time",lambda:original["created_at"]+8*3600)
    token=testing.activate(db,code.lower().replace("-"," "),"127.0.0.1")
    assert testing.session(db,token)["id"]==original["id"]
    assert testing.view(original)["paid_rub"]=="800"
    assert not any("code" in k for k in testing.view(original))


def test_topup_is_exact_difference_frozen_and_only_credited_once(db,payment):
    original,code=payment
    gift={"id":"gift","price_ton":"9"}
    sale=price(db,"9")["sale_price"]
    chosen=testing.choose(db,original,gift,sale)
    assert chosen["topup_rub"]==str(sale-800)
    with pytest.raises(ValueError,match="зафиксирован"):
        testing.choose(db,original,{"id":"other","price_ton":"10"},1000)
    current=db.get("catalog_test_payment",original["id"])
    assert testing.confirm_topup(db,current)["state"]=="TEST_COMPLETE"
    with pytest.raises(ValueError):testing.confirm_topup(db,current)
    assert len(db.get("catalog_test_payment",original["id"])["payments"])==2
    assert db.list("task")==[]
    assert db.list("grant")==[] and db.list("order")==[] and db.list("catalog_checkout")==[]


def test_cheaper_gift_keeps_balance_and_code_for_next_purchase(db,payment):
    original,code=payment
    result=testing.choose(db,original,{"id":"cheap","price_ton":"4"},price(db,"4")["sale_price"])
    sale=price(db,"4")["sale_price"]
    assert result["state"]=="TEST_COMPLETE" and result["paid_rub"]=="800"
    assert result["balance_rub"]==str(800-sale)
    token=testing.activate(db,code,"127.0.0.1")
    restored=testing.session(db,token)
    with pytest.raises(ValueError,match="уже получен"):testing.choose(db,restored,{"id":"cheap","price_ton":"4"},sale)
    chosen=testing.choose(db,restored,{"id":"next","price_ton":"4"},sale)
    assert chosen["topup_rub"]==str(sale-(800-sale))
    completed=testing.confirm_topup(db,db.get("catalog_test_payment",original["id"]))
    assert completed["balance_rub"]=="0"
    assert completed["purchased_ids"]==["cheap","next"]


def test_old_completed_test_preserves_remainder_and_can_reactivate(db,payment):
    original,code=payment
    db.put("catalog_test_payment",original["id"],{"state":"TEST_COMPLETE","selected":{"id":"old"},"selected_price":600})
    token=testing.activate(db,code,"127.0.0.1")
    restored=testing.view(testing.session(db,token))
    assert restored["balance_rub"]=="200" and restored["purchased_ids"]==["old"]


def test_balance_can_cover_multiple_gifts_without_double_spending(db,payment):
    original,code=payment
    sale=price(db,"1")["sale_price"]
    testing.choose(db,original,{"id":"one","price_ton":"1"},sale)
    current=db.get("catalog_test_payment",original["id"])
    testing.choose(db,current,{"id":"two","price_ton":"1"},sale)
    result=testing.view(db.get("catalog_test_payment",original["id"]))
    assert result["balance_rub"]==str(800-2*sale)
    assert len(result["purchased_ids"])==2


def test_stale_balance_update_cannot_overwrite_new_purchase(db,payment):
    original,code=payment
    testing.choose(db,original,{"id":"one","price_ton":"1"},price(db,"1")["sale_price"])
    assert not testing.save_current(db,original,{"purchases":[]})
    assert len(testing.view(db.get("catalog_test_payment",original["id"]))["purchased_ids"])==1


def test_expiration_releases_selection(db,payment):
    original,code=payment
    testing.choose(db,original,{"id":"gift","price_ton":"9"},price(db,"9")["sale_price"])
    db.put("catalog_test_payment",original["id"],{"expires_at":time.time()-1})
    token=testing.activate(db,code,"127.0.0.1")
    current=testing.session(db,token)
    assert current["state"]=="AWAITING_SELECTION" and current["selected"] is None


def test_two_devices_cannot_choose_twice(db,payment):
    original,code=payment
    def choose(n):
        try:return testing.choose(db,original,{"id":str(n),"price_ton":"9"},price(db,"9")["sale_price"])
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(choose,[1,2]))
    assert sum(r is not None for r in results)==1


def test_invalid_code_attempts_have_persistent_delay(db,payment):
    with pytest.raises(ValueError,match="недействителен"):testing.activate(db,"WRONG-CODE","ip")
    with pytest.raises(ValueError,match="Слишком много"):testing.activate(db,payment[1],"ip")


def test_confirmed_recipient_is_saved_per_gift_and_old_history_is_preserved(db,payment):
    original,code=payment
    sale=price(db,"1")["sale_price"]
    testing.choose(db,original,{"id":"first","price_ton":"1"},sale,"@first_buyer")
    current=db.get("catalog_test_payment",original["id"])
    assert current["recipient"]=="first_buyer"
    testing.choose(db,current,{"id":"second","price_ton":"1"},sale,"second_buyer")
    current=db.get("catalog_test_payment",original["id"])
    assert current["recipient"]=="second_buyer"
    assert [p["recipient"] for p in current["purchases"]]==["first_buyer","second_buyer"]
    assert testing.view(current)["balance_rub"]==str(800-2*sale)


def test_invalid_recipient_does_not_spend_balance(db,payment):
    original,code=payment
    with pytest.raises(ValueError,match="юзернейм"):
        testing.choose(db,original,{"id":"gift","price_ton":"1"},price(db,"1")["sale_price"],"not a username")
    assert testing.view(db.get("catalog_test_payment",original["id"]))["balance_rub"]=="800"
    assert testing.purchases(db.get("catalog_test_payment",original["id"]))==[]
