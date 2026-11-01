import time

import pytest

from backend.app import funded_orders as funded


@pytest.fixture
def locked_selection(db):
    db.configure(emergency_stop=False)
    gift={"id":"nft-one","price_ton":"4.01","slug":"ChillFlame-5"}
    grant=db.put("grant","paid-order",{"state":"AWAITING_TOPUP","recipient":"buyer","selected":gift,"selected_price":700,"revision":1,"checkout_id":"topup"})
    db.put("grant_payment","original",{"grant_id":grant["id"],"accepted":True,"status":"PAID","amount_rub":"500"})
    checkout=db.put("catalog_checkout","topup",{"state":"READY","funding_grant_id":grant["id"],"funding_revision":1,"gift":gift,"shown_price":700,"topup_rub":"200","submitted_at":time.time(),"lot_id":"published-lot","url":"https://playerok.com/products/topup","expires_at":time.time()+900})
    return grant,checkout


def test_published_topup_keeps_exact_amount_and_gift_after_prices_change(db,locked_selection):
    grant,checkout=locked_selection
    db.configure(manual_ton_rub="200",catalog_profit_value="40",stars_rub="2")
    with pytest.raises(ValueError,match="зафиксирован"):
        funded.select_gift(db,grant,{"id":"another-nft","price_ton":"9"},1200)
    view=funded.public_view(db,db.get("grant",grant["id"]))
    assert view["selection_locked"]
    assert view["selected_id"]=="nft-one"
    assert view["selected_price"]==700
    assert view["topup_rub"]=="200"
    assert view["payment_url"]==checkout["url"]
    assert db.get("catalog_checkout",checkout["id"])["shown_price"]==700


def test_delayed_price_callback_cannot_unlock_published_topup(db,locked_selection):
    grant,checkout=locked_selection
    funded.checkout_result(db,{**checkout,"state":"PRICE_CHANGED"})
    assert db.get("grant",grant["id"])["state"]=="AWAITING_TOPUP"


def test_clock_expiry_does_not_unlock_until_listing_is_confirmed_removed(db,locked_selection):
    grant,checkout=locked_selection
    db.put("catalog_checkout",checkout["id"],{"expires_at":time.time()-10})
    funded.checkout_result(db,db.get("catalog_checkout",checkout["id"]))
    assert db.get("grant",grant["id"])["state"]=="AWAITING_TOPUP"
    db.put("catalog_checkout",checkout["id"],{"state":"EXPIRED"})
    funded.checkout_result(db,db.get("catalog_checkout",checkout["id"]))
    assert db.get("grant",grant["id"])["state"]=="AWAITING_SELECTION"


@pytest.mark.parametrize("state",["UNKNOWN","FAILED","PRICE_CHANGED"])
def test_uncertain_published_topup_requires_review_without_changing_terms(db,locked_selection,state):
    grant,checkout=locked_selection
    db.put("catalog_checkout",checkout["id"],{"state":state})
    funded.checkout_result(db,db.get("catalog_checkout",checkout["id"]))
    current=db.get("grant",grant["id"])
    assert current["state"]=="REVIEW"
    assert current["selected"]==grant["selected"]
    assert current["selected_price"]==700
    assert funded.public_view(db,current)["selection_locked"]


def test_callback_for_old_topup_cannot_unlock_new_selection(db,locked_selection):
    grant,checkout=locked_selection
    db.put("grant",grant["id"],{"checkout_id":"new-topup"})
    db.put("catalog_checkout",checkout["id"],{"state":"EXPIRED"})
    funded.checkout_result(db,db.get("catalog_checkout",checkout["id"]))
    assert db.get("grant",grant["id"])["state"]=="AWAITING_TOPUP"
