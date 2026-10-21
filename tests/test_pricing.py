import pytest
from backend.app.pricing import calculate,PricingError,D

PROFILE={"configured":True,"fees":[{"name":"фиксированная","type":"fixed","value":"25"},
    {"name":"10%","type":"percent","value":"10","basis":"remaining"},
    {"name":"5%","type":"percent","value":"5","basis":"remaining"}]}

@pytest.mark.parametrize("purchase,price",[(800,1012),(1000,1258),(1200,1503),(1500,1872)])
def test_spec_formula(purchase,price):
    r=calculate(purchase,0,PROFILE,{"profit_value":5})
    assert r["sale_price"]==price
    assert D(r["profit_rub"])>=D(purchase)*D('.05')

def test_sale_profit_basis_and_fixed_target():
    zero={"configured":True,"fees":[]}
    assert calculate(100,0,zero,{"profit_value":20,"profit_basis":"sale"})["sale_price"]==125
    assert calculate(100,10,zero,{"profit_type":"rub","profit_value":30})["sale_price"]==140

def test_competitor_cannot_break_safe_floor():
    assert calculate(1000,0,PROFILE,{"profit_value":5,"competitor_offset":100},100)["sale_price"]==1258

def test_competitor_higher_rounding_and_maximum():
    zero={"configured":True,"fees":[]}
    r=calculate(100,0,zero,{"profit_value":5,"competitor_mode":"higher","competitor_offset_type":"percent","competitor_offset":10,"round_step":10},123)
    assert r["sale_price"]==140
    with pytest.raises(PricingError):calculate(1000,0,PROFILE,{"max_price":100})

def test_fixed_in_base_is_explicit():
    p={"configured":True,"fees":[{"type":"fixed","value":25},{"type":"percent","value":10,"include_fixed":True}]}
    assert calculate(1000,0,p,{"profit_value":0})["sale_price"]==1137

def test_later_percentage_excludes_effective_fixed_part():
    p={"configured":True,"fees":[{"type":"fixed","value":25},
        {"type":"percent","value":10,"include_fixed":True},
        {"type":"percent","value":5,"include_fixed":False}]}
    # net = .90 * (sale - 25) - .05 * (.90 * sale)
    assert calculate(1000,0,p,{"profit_value":0})["sale_price"]==1196

@pytest.mark.parametrize("value",["NaN","Infinity","invalid"])
def test_non_finite_rejected(value):
    with pytest.raises(PricingError):D(value)

def test_unconfigured_and_impossible_fees_rejected():
    with pytest.raises(PricingError):calculate(100,0,{}, {})
    with pytest.raises(PricingError):calculate(100,0,{"configured":True,"fees":[{"type":"percent","value":100}]},{})
