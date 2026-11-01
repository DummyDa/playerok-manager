from backend.app.schemas import ProfitCalculation
from backend.app.profit_calculator import estimate_profit
from backend.app.pricing import D


def test_calculator_uses_current_saved_fees_and_all_costs(db):
    db.configure(ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1.26")
    fees=[{"name":"Playerok","type":"percent","value":10,"basis":"sale"},{"name":"Вывод","type":"percent","value":6,"basis":"remaining"},{"name":"TON","type":"ton","value":"0.3"}]
    db.put("fees","default",{"configured":True,"fees":fees})
    p=ProfitCalculation(sale_rub=635,purchase="4.01",premium_enabled=True,premium_rub=13)
    result=estimate_profit(db,p)
    assert D(result["profit_rub"])==D("61.71")
    assert D(result["fees_rub"])==D("127.79")
    assert D(result["promotion_rub"])==13 and len(result["fees"])==3
    fees[1]["value"]=8
    db.put("fees","default",{"fees":fees})
    assert D(estimate_profit(db,p)["profit_rub"])<D(result["profit_rub"])
    assert db.list("operation")==[] and db.list("ledger")==[]


def test_calculator_ruble_costs_and_negative_result(db):
    db.configure(ton_rate_mode="manual",manual_ton_rub="100",stars_rub="1.26")
    db.put("fees","default",{"configured":True,"fees":[]})
    p=ProfitCalculation(sale_rub=400,purchase=450,purchase_currency="RUB")
    assert D(estimate_profit(db,p)["profit_rub"])==D("-81.5")
    assert D(estimate_profit(db,p)["promotion_rub"])==0
