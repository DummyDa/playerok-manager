"""Stable deal creation time; polling updates must not reorder old orders."""
from datetime import datetime, timezone
from uuid import UUID


def order_created_at(order):
    value=order.get("created_at")
    if value is not None:
        try:
            if isinstance(value,(int,float)) and not isinstance(value,bool):return float(value)
            date=value if isinstance(value,datetime) else datetime.fromisoformat(value.replace("Z","+00:00"))
            if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
            return date.timestamp()
        except (ValueError,TypeError,AttributeError,OverflowError):pass
    # Playerok uses UUIDv6-style time-ordered IDs (including non-RFC variants).
    # Read the layout bits directly rather than UUID.version, which is None
    # for those variants. Recover the time
    # for existing imported records whose API summary omitted createdAt.
    try:
        identifier=UUID(str(order["id"]))
        version=(identifier.int>>76)&0xf
        if version==6:
            ticks=((identifier.int>>80)<<12)|((identifier.int>>64)&0xfff)
            return (ticks-0x01b21dd213814000)/10_000_000
        if version==1:return (identifier.time-0x01b21dd213814000)/10_000_000
        if version==7:return (identifier.int>>80)/1000
    except (ValueError,TypeError,KeyError,AttributeError):pass
    return float(order.get("discovered_at") or 0)


def newest_orders(orders):
    return sorted(orders,key=lambda order:(order_created_at(order),order["id"]),reverse=True)
