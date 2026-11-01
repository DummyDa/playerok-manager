import time
import pytest
from decimal import ROUND_CEILING
from backend.app.catalog_pricing import price, context, public_policy
from backend.app.pricing import D


@pytest.mark.parametrize("kind,value",[("percent","10"),("rub","100")])
def test_catalog_profit_after_every_cost(db,kind,value):
    db.configure(stars_rub="1.26",catalog_profit_type=kind,catalog_profit_value=value,catalog_premium_rub="13")
    db.put("fx","ton-rub",{"rate":"100","source_at":time.time()})
    db.put("fees","default",{"configured":True,"fees":[{"type":"percent","value":"10","basis":"sale"},{"type":"ton","value":"0.25"},{"type":"percent","value":"5","basis":"purchase"}]})
    for ton in ["1","4.182","100"]:
        r=price(db,ton);purchase=D(ton)*100
        assert D(r["promotion_rub"])==0  # old saved premium settings are ignored
        assert D(r["profit_rub"]) >= (purchase/10 if kind=="percent" else 100)
        assert D(r["net_rub"])-purchase-D("31.5")==D(r["profit_rub"])
        c=context(db)
        assert r["sale_price"]==int((D(ton)*c["ton_multiplier"]+c["fixed_rub"]).to_integral_value(rounding=ROUND_CEILING))


def test_catalog_fails_closed_when_costs_missing(db):
    assert public_policy(db)["ready"] is False
    db.configure(ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1",catalog_premium_rub=0)
    assert public_policy(db)["ready"] is False
    db.put("fees","default",{"configured":True,"fees":[]})
    assert public_policy(db)["ready"] is True
