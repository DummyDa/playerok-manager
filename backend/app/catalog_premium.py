"""One premium tariff per advertising sale; allocations are not payment records."""
import time

from .pricing import D, PricingError


def quote(service, lot_id, amount):
    try:
        tariff = service.premium_quote(lot_id, amount)
        cost = D(tariff["cost_rub"])
        if not cost.is_finite() or cost < 0:
            raise ValueError("Invalid premium tariff")
        return {"premium_rub": str(cost), "premium_basis": "playerok_tariff", "premium_pending": False,
                "premium_price_rub": str(amount), "premium_checked_at": time.time()}
    except Exception:
        return {"premium_pending": True, "premium_retry_at": time.time() + 60}


def refresh(service, limit=1):
    """Also fill historical advertising receipts without replaying any trade."""
    count = 0
    for receipt in service.db.list("catalog_credit_receipt", 100000):
        if not eligible(service.db, receipt) or receipt.get("premium_rub") is not None:
            continue
        if receipt.get("premium_retry_at", 0) > time.time():
            continue
        source = service.db.get("order", receipt["id"]) or {}
        lot_id = receipt.get("lot_id") or source.get("lot_id")
        if not lot_id:
            continue
        service.db.put("catalog_credit_receipt", receipt["id"], quote(service, lot_id, receipt["amount_rub"]))
        count += 1
        if count >= limit:
            break


def eligible(db, receipt, wallets=None, lots=None, orders=None):
    if not receipt.get("accepted") or receipt.get("kind") != "ORIGINAL":
        return False
    wallet = (wallets.get(receipt.get("credit_id")) if wallets is not None else db.get("catalog_credit_payment", receipt.get("credit_id"))) or {}
    if wallet.get("test") or wallet.get("source") != "playerok_promotional":
        return False
    source = (orders.get(receipt["id"]) if orders is not None else db.get("order", receipt["id"])) or {}
    lot_id = receipt.get("lot_id") or source.get("lot_id")
    lot = (lots.get(lot_id) if lots is not None else db.get("lot", lot_id)) or {}
    return not (lot.get("lot_type") == "TEMPORARY" or any(lot.get(k) for k in
                ("catalog_checkout_id", "catalog_gift_id", "catalog_credit_id", "funding_purpose")))


def allocations(db, orders=None):
    orders = db.list("order", 100000) if orders is None else orders
    order_map = {o["id"]: o for o in orders}
    wallets = {w["id"]: w for w in db.list("catalog_credit_payment", 100000)}
    lots = {l["id"]: l for l in db.list("lot", 100000)}
    deliveries = {}
    for order in orders:
        if order.get("catalog_credit_id"):
            deliveries.setdefault(order["catalog_credit_id"], []).append(order)
    result = []
    for receipt in db.list("catalog_credit_receipt", 100000):
        if not eligible(db, receipt, wallets, lots, order_map):
            continue
        candidates = sorted(deliveries.get(receipt["credit_id"], []),
                            key=lambda o: (o.get("created_at", 0), o["id"]))
        # A failed selection may be replaced. Charge its sale to the first
        # successful delivery, or the current attempt while still in progress.
        source = order_map.get(receipt["id"]) or {}
        paid_at = source.get("created_at", 0)
        candidates = [o for o in candidates if not paid_at or o.get("created_at", 0) >= paid_at]
        if not candidates:
            continue
        target = next((o for o in candidates if o.get("catalog_settled") or o.get("stage") == "COMPLETE"), candidates[-1])
        result.append({"id": "catalog-premium:" + receipt["id"], "type": "promotion",
                       "order_id": target["id"], "receipt_id": receipt["id"],
                       "amount_rub": receipt.get("premium_rub"), "pending": receipt.get("premium_rub") is None,
                       "basis": "playerok_tariff", "updated_at": target.get("completed_at", target.get("created_at", 0))})
    return result


def order_cost(db, order_id):
    entries = [e for e in allocations(db) if e["order_id"] == order_id]
    if any(e["pending"] for e in entries):
        raise PricingError("Ожидается тариф премиума рекламного лота. Прибыль без него не рассчитана")
    return sum((D(e["amount_rub"]) for e in entries), D(0))


def projected_ledger(db, ledger, orders=None):
    """Read-only cost projection. Never add a second real charge to the ledger."""
    existing = {e.get("id") for e in ledger}
    return [*ledger, *(e for e in allocations(db, orders) if not e["pending"] and e["id"] not in existing)]
