import pytest
from backend.app.pricing import calculate,calculate_with_premium,PricingError,D

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

def test_premium_is_separate_from_fees_and_reduces_profit():
    p={"configured":True,"fees":[{"type":"percent","value":10,"basis":"sale"}]}
    result=calculate(100,10,p,{"profit_type":"rub","profit_value":20},promotion_rub=50)
    assert result["sale_price"]==200
    assert D(result["fees_rub"])==20 and D(result["promotion_rub"])==50
    assert D(result["profit_rub"])==20

def test_price_dependent_premium_is_quoted_for_final_price():
    prices=[]
    def tariff(price):prices.append(price);return D(price)*D(".1")
    result=calculate_with_premium(100,0,{"configured":True,"fees":[]},{"profit_value":5},tariff)
    assert result["sale_price"]==117
    assert prices[-1]==result["sale_price"]
    assert D(result["promotion_rub"])==D("11.7")
    assert D(result["profit_rub"])>=5

def test_stepped_premium_and_sale_profit_basis_and_competitor_floor():
    def tariff(price):return 10 if price<120 else 30
    p={"configured":True,"fees":[]}
    result=calculate_with_premium(100,0,p,{"profit_type":"rub","profit_value":15},tariff)
    assert result["sale_price"]==145 and D(result["promotion_rub"])==30
    result=calculate_with_premium(100,0,p,{"profit_value":20,"profit_basis":"sale"},lambda price:D(price)*D(".1"),competitor_price=1)
    assert D(result["profit_rub"])>=D(result["sale_price"])*D(".2")
    assert result["sale_price"]==143
    with pytest.raises(PricingError,match="максимум"):
        calculate_with_premium(100,0,p,{"max_price":110},lambda price:25)

def test_unresolvable_premium_blocks_pricing_instead_of_understating_cost():
    with pytest.raises(PricingError,match="согласовать"):
        calculate_with_premium(100,0,{"configured":True,"fees":[]},{},lambda price:price)


def test_ton_fee_and_percentages_precede_purchase_deduction():
    from backend.app.pricing import net_coefficients
    fees=[{"type":"percent","value":10,"basis":"sale"},
          {"type":"ton","value":"0.5"},
          {"type":"percent","value":5,"basis":"remaining","include_fixed":True}]
    a,b=net_coefficients(fees,400,100)
    # 1000 - 10% = 900; minus .5 TON = 850; minus 5% = 807.50.
    net=a*D(1000)+b
    assert net==D("807.50")
    assert net-D(400)-D(25)-D(13)==D("369.50")
    r=calculate(400,25,{"configured":True,"fees":fees},
                {"profit_type":"rub","profit_value":20},promotion_rub=13,ton_rub=100)
    assert D(r["profit_rub"])>=20
    assert D(r["net_rub"])-400-25-13==D(r["profit_rub"])


def test_ton_fee_requires_rate_and_preserves_fixed_fee_basis_choice():
    from backend.app.pricing import net_coefficients
    fees=[{"type":"ton","value":"0.25"},{"type":"percent","value":10}]
    with pytest.raises(PricingError,match="курс TON"):net_coefficients(fees,100)
    a,b=net_coefficients(fees,100,100)
    assert a*1000+b==875  # fixed fees excluded from percentage base by default
    fees[1]["include_fixed"]=True
    a,b=net_coefficients(fees,100,100)
    assert a*1000+b==D("877.5")
    result=calculate_with_premium(100,0,{"configured":True,"fees":fees},{},lambda price:13,ton_rub=100)
    assert D(result["profit_rub"])>=5
