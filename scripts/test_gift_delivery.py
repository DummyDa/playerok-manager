"""Interactive, one-gift live test of the Kurigram delivery adapter."""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Config
from backend.app.db import Database, uid
from backend.app.integrations.clients import Mrkt
from backend.app.integrations.kurigram_delivery import KurigramDelivery
from backend.app.process_lock import process_lock
from backend.app.runtime_config import has_user_session
from backend.app.safety import IntegrationError, ManualRequired


class ManualTestContext:
    """Permit this explicitly confirmed transfer without enabling the worker."""
    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db, name)

    def settings(self):
        return {**self.db.settings(), "transfers_enabled": True}


def username(value):
    value = value.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value):
        raise ValueError("Введите username получателя, например @username")
    return value


def prepare(db, adapter, recipient):
    reserved = {o.get("gift_slug") for o in db.list("order", 100000)}
    reserved.update(j.get("slug") for j in db.list("browser_job", 100000)
                    if j.get("state") not in {"COMPLETE", "CANCELLED", "FAILED"})

    async def read(client):
        from pyrogram import raw
        me = await client.get_me()
        user = await client.get_users(recipient)
        if not user or user.is_bot or user.is_deleted or user.id == me.id:
            raise ManualRequired("Выберите другого активного пользователя, не бота и не свой аккаунт")
        candidates, offset, offsets = [], "", set()
        while True:
            page = await client.invoke(raw.functions.payments.GetSavedStarGifts(
                peer=raw.types.InputPeerSelf(), offset=offset, limit=100), retries=1)
            for saved in page.gifts:
                gift = saved.gift
                if (not isinstance(gift, raw.types.StarGiftUnique)
                        or not saved.msg_id or not gift.slug or gift.slug in reserved
                        or saved.refunded or gift.burned or gift.resell_amount
                        or getattr(gift.owner_id, "user_id", None) != me.id
                        or (saved.can_transfer_at or 0) > time.time()):
                    continue
                candidates.append({"msg_id": saved.msg_id, "slug": gift.slug,
                                   "transfer_stars": saved.transfer_stars or 0,
                                   "can_transfer_at": saved.can_transfer_at or 0})
            offset = page.next_offset
            if not offset:
                break
            if offset in offsets:
                raise IntegrationError("Telegram вернул повторный offset списка подарков")
            offsets.add(offset)
        if not candidates:
            raise ManualRequired("Нет доступных личных NFT: подарки заказов, выставленные на продажу и заблокированные исключены")
        candidates.sort(key=lambda gift: gift["slug"].casefold())
        return {"gifts": candidates, "recipient": recipient, "recipient_id": user.id,
                "recipient_name": " ".join(filter(None, [user.first_name, user.last_name])),
                "sender_id": me.id, "count": len(candidates)}

    return adapter.telegram(read)


def select_gift(gifts, ask=input, output=print):
    output("Доступные подарки (стоимость передачи указана в Stars):")
    for number, gift in enumerate(gifts, 1):
        output(f"{number}. {gift['slug']} — {gift['transfer_stars']} Stars — https://t.me/nft/{gift['slug']}")
    while True:
        value = ask("Номер подарка (Enter — отмена): ").strip()
        if not value:
            return None
        try:
            number = int(value)
        except ValueError:
            number = 0
        if 1 <= number <= len(gifts):
            return dict(gifts[number - 1])
        output(f"Введите число от 1 до {len(gifts)} или нажмите Enter для отмены.")


def reconcile_tests(db, mrkt, output=print):
    unresolved = [j for j in db.list("delivery_test", 100000) if j.get("state") in {"SUBMITTED", "UNKNOWN"}]
    sender_id = None
    for job in unresolved[:]:
        owner = mrkt.owner(job["slug"])
        if owner == job["recipient_id"]:
            db.put("delivery_test", job["id"], {"state": "COMPLETE", "reconciled": True})
            unresolved.remove(job)
            output(f"Предыдущая передача {job['slug']} подтверждена.")
            continue
        if owner is None and job.get("submitted_at"):
            receipt = mrkt.transfer_receipt(job["slug"], job.get("recipient") or job["recipient_id"], job["recipient_id"],
                                            job["submitted_at"], job.get("msg_id", 0))
            if receipt:
                db.put("delivery_test", job["id"], {"state": "COMPLETE", "reconciled": True,
                                                    "verification": "telegram_service_message", "receipt": receipt})
                unresolved.remove(job)
                output(f"Передача {job['slug']} подтверждена сообщением Telegram; ID владельца скрыт.")
                continue
        # Allow server processing to settle before deciding a transfer failed.
        submitted = job.get("submitted_at") or job["updated_at"]
        if time.time() - submitted < 120:
            output(f"Для сверки {job['slug']} подождите ещё {max(1, int(120 - (time.time() - submitted)))} сек.")
            continue
        if sender_id is None:
            async def current_account(client):
                return (await client.get_me()).id
            sender_id = mrkt.telegram(current_account)
        # A unique owner match plus the gift in our saved inventory is positive
        # evidence; missing/private ownership alone never clears an unknown write.
        if owner == sender_id and (not job.get("sender_id") or job["sender_id"] == sender_id):
            saved = mrkt.saved(job["slug"])
            if saved and (not job.get("msg_id") or saved["msg_id"] == job["msg_id"]):
                db.put("delivery_test", job["id"], {"state": "NOT_EXECUTED", "reconciled": True,
                    "reconciled_owner_id": sender_id})
                unresolved.remove(job)
                output(f"{job['slug']} остался у вас. Предыдущий тест закрыт; можно запустить новый.")
    if unresolved:
        links = ", ".join("https://t.me/nft/" + j["slug"] for j in unresolved)
        raise ManualRequired("Предыдущий тест требует сверки владельца; автоматического повтора нет: " + links)


def run_test(db, recipient, ask=input, output=print):
    if db.settings()["emergency_stop"]:
        raise ManualRequired("Снимите аварийную остановку в панели перед ручным тестом")
    if not has_user_session(db):
        raise IntegrationError("Сначала подключите личный Telegram в настройках проекта")
    mrkt = Mrkt(db)
    reconcile_tests(db, mrkt, output)
    context = ManualTestContext(db)
    adapter = KurigramDelivery(context, mrkt)
    plan = prepare(db, adapter, recipient)
    output(f"Аккаунт отправителя: ID {plan['sender_id']}")
    output(f"Получатель: @{recipient} — {plan['recipient_name']} (ID {plan['recipient_id']})")
    saved = select_gift(plan["gifts"], ask, output)
    if saved is None:
        output("Отменено. Подарок не отправлен.")
        return False
    if saved["transfer_stars"]:
        form = mrkt.transfer_form(saved, recipient)
        if form["recipient_id"] != plan["recipient_id"] or form["currency"] != "XTR":
            raise ManualRequired("Получатель или валюта передачи изменились")
        saved["transfer_stars"] = form["stars"]
    output(f"Выбран подарок: https://t.me/nft/{saved['slug']}")
    output(f"Стоимость передачи: {saved['transfer_stars']} Stars")
    if ask("Отправить этот подарок? Введите ОТПРАВИТЬ (Enter — отмена): ").strip().upper() != "ОТПРАВИТЬ":
        output("Отменено. Подарок не отправлен.")
        return False
    # Re-read the selected gift before sending. The adapter rechecks recipient
    # identity and the exact payment quote immediately before the write.
    fresh = mrkt.saved(saved["slug"])
    if (not fresh or fresh["msg_id"] != saved["msg_id"]
            or fresh["transfer_stars"] != saved["transfer_stars"]
            or fresh["can_transfer_at"] > time.time()):
        raise ManualRequired("Подарок или условия передачи изменились. Ничего не отправлено")
    test_id = uid()
    submitted_at = time.time()
    db.put("delivery_test", test_id, {"state": "SUBMITTED", "slug": saved["slug"],
        "recipient": recipient, "recipient_id": plan["recipient_id"], "stars": saved["transfer_stars"],
        "sender_id": plan["sender_id"], "msg_id": saved["msg_id"], "submitted_at": submitted_at})
    receipt = None
    verification = "owner_id"
    try:
        adapter.transfer(saved, recipient, plan["recipient_id"])
        owner = mrkt.owner(saved["slug"])
        if owner != plan["recipient_id"]:
            if owner is None:
                receipt = mrkt.transfer_receipt(saved["slug"], recipient, plan["recipient_id"],
                                                submitted_at, saved["msg_id"])
            if not receipt:
                raise ManualRequired("Telegram пока не подтвердил передачу: нет ID владельца или подходящего служебного сообщения")
            verification = "telegram_service_message"
    except Exception as exc:
        db.put("delivery_test", test_id, {"state": "UNKNOWN", "error": db.redact(exc)})
        output("Результат требует сверки. Повторная отправка не выполняется.")
        raise
    db.put("delivery_test", test_id, {"state": "COMPLETE", "verification": verification, "receipt": receipt})
    db.log("delivery_test", "Тест Kurigram: подарок передан", f"{saved['slug']} → ID {plan['recipient_id']}")
    output("Готово: подарок передан, новый владелец подтверждён." if verification == "owner_id" else
           "Готово: передача нужному получателю подтверждена служебным сообщением Telegram. ID владельца скрыт.")
    output("Разрешения автоматизации не изменены. Подтвердите проверку выдачи в панели самостоятельно.")
    return True


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Выбрать личную NFT по номеру и отправить через Kurigram после подтверждения")
    parser.add_argument("--recipient", help="Username получателя; иначе скрипт спросит его")
    args = parser.parse_args()
    db = None
    try:
        recipient = username(args.recipient or input("Username получателя (@username): "))
        config = Config()
        if not config.db_path.exists() or not config.key_path.exists():
            raise IntegrationError("Сначала настройте проект и подключите личный Telegram")
        # Prevent simultaneous use of the shared Telegram session by the worker
        # or another live test. This also prevents new order reservations.
        with process_lock(config.data / "worker.lock"):
            db = Database(config)
            run_test(db, recipient)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nТест остановлен. Если отправка уже началась, проверьте владельца подарка; не повторяйте её вслепую.")
        return 1
    except Exception as exc:
        message = db.redact(exc) if db else str(exc)
        if message == "Процесс этого проекта уже запущен":
            message = "Сначала запустите stop.bat: тест использует ту же Telegram-сессию, что и воркер"
        print("Ошибка: " + message)
        return 1
    finally:
        if db:
            db.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
