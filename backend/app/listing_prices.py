from .pricing import D


def validate_price(lot,price):
    raw=lot.get("raw_price")
    if raw is None:return
    raw=D(raw);price=D(price)
    if raw<=0 or price==raw:return
    if price>raw:
        raise ValueError(f"Playerok не позволяет повысить базовую цену {raw} ₽ через редактирование скидки. Для новой базовой цены создайте новый лот.")
    if price>raw*D("0.95"):
        raise ValueError(f"Минимальная скидка Playerok — 5%. Укажите не больше {int(raw*D('0.95'))} ₽ или {raw} ₽ без скидки.")
