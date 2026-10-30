from decimal import Decimal, ROUND_CEILING, InvalidOperation

class PricingError(ValueError): pass
class PremiumQuoteRequired(PricingError): pass
class FxQuoteRequired(PricingError): pass

def D(value):
    try:
        x = Decimal(str(value))
        if not x.is_finite(): raise ValueError()
        return x
    except (InvalidOperation, ValueError, TypeError): raise PricingError("Некорректное денежное значение")

def effective_fx(db):
    """Use the same selected exchange rate in estimates and dashboard responses."""
    settings = db.settings()
    if settings.get("ton_rate_mode", "auto") == "manual":
        rate = D(settings.get("manual_ton_rub"))
        if rate <= 0: raise PricingError("Задайте положительный ручной курс TON")
        return {"rate": str(rate), "source": "Ручной курс", "source_at": settings.get("updated_at", 0), "manual": True}
    return db.get("fx", "ton-rub")


def net_coefficients(fees, purchase, ton_rub=None):
    # Commissions reduce sale proceeds first; purchase is subtracted by calculate.
    a, b, fixed = D(1), D(0), D(0)
    for f in fees:
        if not f.get("enabled", True): continue
        v = D(f.get("value", 0))
        if v < 0: raise PricingError("Комиссия не может быть отрицательной")
        if f.get("type") in ("fixed", "ton"):
            if f.get("type") == "ton":
                if ton_rub is None or D(ton_rub) <= 0: raise PricingError("Для комиссии в TON нужен курс TON")
                v *= D(ton_rub)
            b -= v; fixed += v
        elif f.get("type") == "percent":
            if v >= 100: raise PricingError("Процент комиссии должен быть меньше 100")
            rate = v / 100
            basis = f.get("basis", "remaining")
            if basis == "sale": x, y = D(1), D(0)
            elif basis == "purchase": x, y = D(0), D(purchase)
            elif basis == "remaining": x, y = a, b + (D(0) if f.get("include_fixed", False) else fixed)
            else: raise PricingError("Неизвестная база комиссии")
            a -= rate*x; b -= rate*y
            if basis == "remaining" and f.get("include_fixed",False):fixed *= 1-rate
        else: raise PricingError("Неизвестный тип комиссии")
    if a <= 0: raise PricingError("Комиссии поглощают всю выручку")
    return a, b

def calculate(purchase_rub, transfer_rub, profile, strategy, competitor_price=None, promotion_rub=0, ton_rub=None):
    if not profile.get("configured"): raise PricingError("Сначала настройте и подтвердите профиль комиссий")
    purchase, transfer, promotion = D(purchase_rub), D(transfer_rub), D(promotion_rub)
    if purchase <= 0 or transfer < 0 or promotion < 0: raise PricingError("Нет достоверной себестоимости")
    a, b = net_coefficients(profile.get("fees", []), purchase, ton_rub)
    target = D(strategy.get("profit_value", 5))
    if target < 0: raise PricingError("Целевая прибыль не может быть отрицательной")
    denominator = a
    if strategy.get("profit_type", "percent") == "rub": gain = target
    elif strategy.get("profit_basis", "purchase") == "sale":
        denominator -= target/100; gain = D(0)
    else: gain = purchase*target/100
    if denominator <= 0: raise PricingError("Указанная прибыль недостижима")
    safe = (purchase + transfer + promotion + gain - b)/denominator
    desired = safe
    if competitor_price is not None:
        competitor = D(competitor_price)
        offset = D(strategy.get("competitor_offset", 0))
        if offset < 0: raise PricingError("Отклонение не может быть отрицательным")
        if strategy.get("competitor_offset_type") == "percent": offset = competitor*offset/100
        mode = strategy.get("competitor_mode", "cheaper")
        desired = max(desired, competitor + (offset if mode == "higher" else -offset if mode == "cheaper" else D(0)))
    step = D(strategy.get("round_step", 1))
    if step < 1 or step != step.to_integral_value(): raise PricingError("Шаг цены должен быть целым числом рублей")
    desired = max(desired, D(strategy.get("min_price", 0)))
    final = (desired/step).to_integral_value(rounding=ROUND_CEILING)*step
    ceiling = strategy.get("max_price")
    if ceiling is not None and final > D(ceiling): raise PricingError("Безопасная цена превышает установленный максимум")
    net = a*final+b
    profit=net-purchase-transfer-promotion
    return {"purchase_rub": str(purchase), "transfer_rub": str(transfer), "promotion_rub":str(promotion), "safe_price": str((safe/step).to_integral_value(rounding=ROUND_CEILING)*step), "sale_price": int(final), "net_rub": str(net), "fees_rub": str(final-net), "profit_rub": str(profit), "profit_percent": str(profit/purchase*100)}

def calculate_with_premium(purchase,transfer,profile,strategy,premium_cost,competitor_price=None,ton_rub=None):
    """Resolve price-dependent promotion cost using exact quotes for every candidate.

    Ascending candidates also handle stepped tariffs. Never return a price quoted
    for a different tariff price or silently use the old listing price's tariff.
    """
    candidate=calculate(purchase,transfer,profile,strategy,competitor_price,ton_rub=ton_rub)["sale_price"]
    for _ in range(16):
        if candidate>10000000:raise PricingError("Цена с учётом премиума превышает допустимый предел")
        cost=D(premium_cost(candidate))
        result=calculate(purchase,transfer,profile,strategy,competitor_price,promotion_rub=cost,ton_rub=ton_rub)
        if result["sale_price"]<=candidate:
            # If a tariff decreased, retain the already validated candidate.
            result=calculate(purchase,transfer,profile,{**strategy,"min_price":max(D(strategy.get("min_price",0)),D(candidate))},competitor_price,promotion_rub=cost,ton_rub=ton_rub)
            return result
        candidate=result["sale_price"]
    raise PricingError("Не удалось согласовать цену с тарифом премиума. Автоматическое изменение заблокировано.")

def required_profit(purchase,sale,strategy):
    target=D(strategy.get("profit_value",5))
    if strategy.get("profit_type","percent")=="rub":return target
    return D(sale if strategy.get("profit_basis","purchase")=="sale" else purchase)*target/100
