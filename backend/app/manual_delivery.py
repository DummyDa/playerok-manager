"""Owner attestation of a completed manual gift transfer; never sends a gift."""
import time
from sqlalchemy import update
from .db import Entity
from .safety import gate, is_nft_lot


def allowed(db, order):
    if not db.settings().get("manual_gift_delivery") or not order:
        return False
    if order.get("cancel_requested") or order.get("manual_delivery_receipt"):
        return False
    if order.get("stage") not in {"AWAITING_MANUAL_DELIVERY", "WITHDRAWN", "MANUAL"}:
        return False
    if not is_nft_lot(db.get("lot", order.get("lot_id"))):
        return False
    if not all(order.get(k) for k in ("gift_slug", "mrkt_gift_id", "recipient_id", "recipient", "snapshot")):
        return False
    ops = [o for o in db.list("operation", 100000) if o.get("order_id") == order["id"]]
    if any(o.get("state") in {"QUEUED", "SUBMITTED", "UNKNOWN", "BROWSER_WAIT"} for o in ops):
        return False
    if order["stage"] == "MANUAL":
        withdrawal = db.get("operation", order["id"] + ":withdraw") or {}
        transfer = db.get("operation", order["id"] + ":transfer") or {}
        # Only an explicit Telegram rejection is known not to have sent a gift.
        if withdrawal.get("state") != "COMPLETE":return False
        if transfer.get("submitted_at") and not (transfer.get("state") == "FAILED" and "STARGIFT_TRANSFER_TOO_EARLY" in (transfer.get("error") or "")):
            return False
    return True


def confirm(service, oid):
    db = service.db
    gate(db)
    order = db.get("order", oid)
    if order and order.get("manual_delivery_receipt"):
        if order.get("stage") != "COMPLETE":
            finish(service, order)
        return {"confirmed": True}
    if not allowed(db, order):
        raise ValueError("Заказ не готов к ручному подтверждению или требует сверки операции")
    if order.get("catalog_credit_id"):
        from .catalog_credit import fresh_order
        fresh_order(service, order, allow_review=True)
    else:
        fresh = service.fresh_order(order)
        if not fresh or fresh["status"] != "PAID":
            raise ValueError("Оплата заказа больше не подтверждена")
    receipt = {"at": time.time(), "slug": order["gift_slug"], "recipient_id": order["recipient_id"], "source": "owner_dashboard"}
    previous = {k: v for k, v in order.items() if k not in {"id", "updated_at"}}
    with db.tx() as session:
        changed = session.execute(update(Entity).where(Entity.kind == "order", Entity.id == oid, Entity.data == previous).values(
            data={**previous, "stage": "TRANSFERRED", "manual_delivery_receipt": receipt, "manual_hold": False,
                  "reason": "Передача подтверждена продавцом вручную", "activity_at": time.time()}, updated=time.time())).rowcount
    if not changed:raise ValueError("Заказ изменился. Обновите страницу и проверьте его состояние")
    finish(service, db.get("order", oid))
    db.log("manual_gift_delivered", "Продавец подтвердил ручную передачу NFT", order["gift_slug"], order_id=oid)
    return {"confirmed": True, "operation_id": oid + ":mark_sent"}


def finish(service, order):
    # Stable ledger key and durable receipt make restart/repeated clicks safe.
    service.ledger(order["id"] + ":transfer", "transfer", order["snapshot"]["transfer_rub"], order["lot_id"], order["id"])
    service.process_order(order["id"])


def buyer_copy(db, text):
    if not db.settings().get("manual_gift_delivery"):return text
    import re
    text = re.sub(r"автовыдача", "ВЫДАЧА ПРОДАВЦОМ", text, flags=re.I)
    text = re.sub(r"автоматическая выдача", "выдача продавцом", text, flags=re.I)
    text = re.sub(r"выдаётся автоматически", "выдаётся продавцом", text, flags=re.I)
    text = re.sub(r"выдается автоматически", "выдаётся продавцом", text, flags=re.I)
    text = re.sub(r"автоматическая передача", "передача продавцом", text, flags=re.I)
    return text
