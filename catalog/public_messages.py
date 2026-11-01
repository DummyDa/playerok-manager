"""Buyer-facing responses; internal causes remain in the owner's database."""
GENERIC="Не удалось выполнить действие. Попробуйте позже или свяжитесь с продавцом в чате Playerok."
SAFE=("Цена изменилась", "Подарок недоступен", "Подарок больше недоступен", "Код недействителен", "Код удалён владельцем", "Введите код", "Некорректный Telegram-юзернейм", "Слишком много", "Вы создали слишком много лотов", "Применён баланс заказа", "Предыдущий выбор уже", "Баланс использован", "Запрос не найден", "Необходимо", "Сессия истекла")


def buyer_message(value):
    if not value:return value
    text=str(value)
    # Only known buyer actions are returned verbatim, never arbitrary upstream errors.
    if len(text)<300 and text.startswith(SAFE) and not any(s in text.lower() for s in ("mrkt","ключ", "token", "прибыл", "api", "traceback", "http")):return text
    return GENERIC


def public_data(value):
    if isinstance(value,list):return [public_data(v) for v in value]
    if not isinstance(value,dict):return value
    result={}
    for key,item in value.items():
        if any(term in key.lower() for term in ("profit","purchase_rub","transfer_rub","fees","commission","promotion_rub","premium_rub","cost_rub","api_key","secret","access_token")):continue
        if key in {"error","notice","delivery_reason","detail"}:
            result[key]=buyer_message(item)
        elif key=="source" and isinstance(item,str) and "mrkt" in item.lower():result[key]="Каталог"
        else:result[key]=public_data(item)
    return result
