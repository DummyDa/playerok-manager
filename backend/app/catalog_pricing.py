"""Shared storefront pricing policy. No credentials are exposed to the browser."""
import time
from .db import DEFAULTS
from .pricing import D, PricingError, effective_fx, net_coefficients, calculate


def context(db):
    settings={**DEFAULTS,**db.settings()}
    fx=effective_fx(db)
    if not fx or (not fx.get("manual") and time.time()-fx.get("source_at",0)>900):
        raise PricingError("Обновите курс TON в основной панели")
    rate=D(fx["rate"])
    if rate<=0:raise PricingError("Некорректный курс TON")
    if settings["stars_rub"] is None:raise PricingError("Задайте стоимость Star в основной панели")
    profile=db.get("fees",settings["catalog_fee_profile_id"]) or {}
    if not profile.get("configured"):raise PricingError("Настройте профиль комиссий каталога в основной панели")
    strategy={"profit_type":settings["catalog_profit_type"],"profit_value":settings["catalog_profit_value"],"profit_basis":"purchase","round_step":1}
    transfer=25*D(settings["stars_rub"])
    premium=D(0)
    a,b0=net_coefficients(profile.get("fees",[]),0,rate)
    _,b=net_coefficients(profile.get("fees",[]),1,rate)
    target=D(strategy["profit_value"])
    percent=target/100 if strategy["profit_type"]=="percent" else D(0)
    fixed_gain=target if strategy["profit_type"]=="rub" else D(0)
    return {"rate":rate,"profile":profile,"strategy":strategy,"transfer":transfer,"premium":premium,
            "ton_multiplier":rate*(1+percent-(b-b0))/a,"fixed_rub":(transfer+premium+fixed_gain-b0)/a}


def public_policy(db):
    try:
        c=context(db)
        return {"ready":True,"ton_multiplier":str(c["ton_multiplier"]),"fixed_rub":str(c["fixed_rub"]),"rate":str(c["rate"]),"transfer_stars":25,"manual_delivery":bool(db.settings().get("manual_gift_delivery"))}
    except PricingError as exc:
        return {"ready":False,"error":str(exc)}
    except Exception:
        return {"ready":False,"error":"Не удалось прочитать настройки цен основной панели"}


def price(db, ton):
    c=context(db)
    return calculate(D(ton)*c["rate"],c["transfer"],c["profile"],c["strategy"],promotion_rub=c["premium"],ton_rub=c["rate"])
