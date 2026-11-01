"""Copy the formerly implicit buyer replies into editable settings once."""
from .catalog_texts import PAID,DELIVERED,PROMO,TOPUP,SUPPORT


def migrate(db):
    if db.get("schema_feature","editable_buyer_messages"):return
    current=db.settings()
    manual="{delivery_notice}"
    values={"buyer_paid_enabled":True,"buyer_delivered_enabled":True,
            "buyer_paid_template":PAID+"\n\n"+manual+"\n\n"+SUPPORT,
            "buyer_delivered_template":DELIVERED+"\n\n"+SUPPORT}
    for key,default in (("catalog_promo_paid_template",PROMO),("catalog_topup_paid_template",TOPUP)):
        text=current.get(key) or default
        text=text.replace("Если подарок дешевле — остаток сохранится на балансе; если дороже — потребуется только доплата.","Одна оплата — один подарок. После выдачи остаток списывается и код закрывается. Допуск без доплаты — 20 ₽; минимальная доплата — 90 ₽.")
        if "{delivery_notice}" not in text:text+="\n\n"+manual
        if "!продавец" not in text:text+="\n\n"+SUPPORT
        if key=="catalog_promo_paid_template" and "VPN" not in text:text+="\n\n🖼 Если картинки не загружаются, попробуйте включить VPN."
        values[key]=text
    db.configure(**values)
    db.put("schema_feature","editable_buyer_messages",{"version":1})
