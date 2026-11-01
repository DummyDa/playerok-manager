"""Owner-only forecast from a sale amount; no market writes."""
from .pricing import D,effective_fx,net_coefficients,PricingError


def estimate_profit(db,p):
    profile=db.get("fees",p.fee_profile_id)
    if not profile or not profile.get("configured"):raise PricingError("Настройте и подтвердите выбранный профиль комиссий")
    rate=D(p.ton_rub) if p.ton_rub is not None else D((effective_fx(db) or {}).get("rate"))
    if rate<=0:raise PricingError("Задайте курс TON в настройках или калькуляторе")
    settings=db.settings()
    stars_price=D(p.stars_rub if p.stars_rub is not None else settings.get("stars_rub"))
    if stars_price<0:raise PricingError("Задайте стоимость Stars")
    stars=p.transfer_stars if p.transfer_stars is not None else settings.get("transfer_stars_reserve",25)
    purchase=D(p.purchase)*(rate if p.purchase_currency=="TON" else D(1))
    if p.premium_enabled and p.premium_rub is None:raise PricingError("Укажите стоимость одного премиум-поднятия")
    premium=D(p.premium_rub or 0)*p.premium_count if p.premium_enabled else D(0)
    sale=D(p.sale_rub);transfer=D(stars)*stars_price;fees=[];enabled=[];previous=sale
    for f in profile.get("fees",[]):
        if not f.get("enabled",True):continue
        enabled.append(f)
        a,b=net_coefficients(enabled,purchase,rate)
        remaining=a*sale+b
        fees.append({"name":f.get("name") or "Комиссия","amount_rub":str(previous-remaining)})
        previous=remaining
    profit=previous-purchase-transfer-premium
    return {"sale_rub":str(sale),"purchase_rub":str(purchase),"transfer_rub":str(transfer),"promotion_rub":str(premium),"fees_rub":str(sale-previous),"net_rub":str(previous),"profit_rub":str(profit),"margin_percent":str(profit/sale*100),"roi_percent":str(profit/purchase*100) if purchase else None,"break_even_rub":str((purchase+transfer+premium-net_coefficients(enabled,purchase,rate)[1])/net_coefficients(enabled,purchase,rate)[0]),"ton_rub":str(rate),"stars_rub":str(stars_price),"transfer_stars":stars,"fees":fees}
