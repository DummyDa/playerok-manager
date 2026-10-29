"""Immediate Telegram withdrawal checks for MRKT gift listings."""
from datetime import datetime, timezone
import time


def gift_block_reason(gift, now=None):
    now = time.time() if now is None else now
    if gift.get("isLocked"):
        return "MRKT заблокировал подарок"
    if gift.get("premarketStatus") == "WaitingGift" or gift.get("isOnPlatform") is False:
        return "Подарок ещё не доступен на MRKT"
    if gift.get("giftType", "Upgraded") != "Upgraded":
        return "Для выдачи нужен коллекционный подарок"
    if "nextTransferDate" not in gift:
        return "MRKT не сообщил срок доступности передачи"
    # exportDate concerns blockchain export; unlockDate/nextGiveAvailableAt
    # concern giveaways. Neither proves a Telegram transfer is locked.
    for field in ("nextTransferDate", "returnLockedUntil"):
        value = gift.get(field)
        if value is None or value == "":
            continue
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            locked = date > datetime.fromtimestamp(now, timezone.utc)
        except (ValueError, TypeError, AttributeError, OverflowError):
            return "MRKT вернул некорректный срок блокировки: " + field
        if locked:
            return "Передача подарка заблокирована до " + value
    return None
