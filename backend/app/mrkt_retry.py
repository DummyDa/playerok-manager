"""Persistent five-minute retry window shared by an order's MRKT steps."""
import time

SECONDS = 300


def deadline(db, order_id, start=False):
    order = db.get("order", order_id) if order_id else None
    value = (order or {}).get("mrkt_retry_deadline")
    if value is None and start:
        value = time.time() + SECONDS
        if order:
            db.put("order", order_id, {"mrkt_retry_deadline": value})
    return value


def expired(db, order_id):
    value = deadline(db, order_id)
    return value is not None and time.time() >= value
