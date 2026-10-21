from decimal import Decimal, ROUND_CEILING, InvalidOperation

class PricingError(ValueError): pass

def D(value):
    try:
        x = Decimal(str(value))
        if not x.is_finite(): raise ValueError()
        return x
    except (InvalidOperation, ValueError, TypeError): raise PricingError("Некорректное денежное значение")

def net_coefficients(fees, purchase):
    a, b, fixed = D(1), D(0), D(0)
    for f in fees:
        if not f.get("enabled", True): continue
        v = D(f.get("value", 0))
        if v < 0: raise PricingError("Комиссия не может быть отрицательной")
        if f.get("type") == "fixed":
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

def calculate(purchase_rub, transfer_rub, profile, strategy, competitor_price=None):
    if not profile.get("configured"): raise PricingError("Сначала настройте и подтвердите профиль комиссий")
    purchase, transfer = D(purchase_rub), D(transfer_rub)
    if purchase <= 0 or transfer < 0: raise PricingError("Нет достоверной себестоимости")
    a, b = net_coefficients(profile.get("fees", []), purchase)
    target = D(strategy.get("profit_value", 5))
    if target < 0: raise PricingError("Целевая прибыль не может быть отрицательной")
    denominator = a
    if strategy.get("profit_type", "percent") == "rub": gain = target
    elif strategy.get("profit_basis", "purchase") == "sale":
        denominator -= target/100; gain = D(0)
    else: gain = purchase*target/100
    if denominator <= 0: raise PricingError("Указанная прибыль недостижима")
    safe = (purchase + transfer + gain - b)/denominator
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
    return {"purchase_rub": str(purchase), "transfer_rub": str(transfer), "safe_price": str((safe/step).to_integral_value(rounding=ROUND_CEILING)*step), "sale_price": int(final), "net_rub": str(net), "fees_rub": str(final-net), "profit_rub": str(net-purchase-transfer), "profit_percent": str((net-purchase-transfer)/purchase*100)}
