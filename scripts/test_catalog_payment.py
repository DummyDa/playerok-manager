"""Create an isolated paid-choice test and optionally send its link to the owner."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.catalog_test_payment import create,catalog_link
from backend.app.config import Config
from backend.app.db import Database
from backend.app.integrations.clients import Bot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lot")
    parser.add_argument("--amount",default="800")
    parser.add_argument("--recipient")
    parser.add_argument("--send",action="store_true",help="Send one message to the configured owner's Telegram chat")
    args=parser.parse_args();db=Database(Config())
    try:
        recipient=args.recipient or db.config.get("OWNER_TELEGRAM_USERNAME")
        if args.send and not db.settings().get("owner_chat_id"):raise ValueError("Owner chat is not configured")
        payment,code=create(db,args.amount,recipient,args.lot)
        link=catalog_link(payment,code)
        if args.send:
            text=f"Оплата получена: {payment['paid_rub']} ₽\nПолучатель: @{payment['recipient']}\n\nВыберите подарок в каталоге:\n{link}\n\nВаш код: {code}\nСсылка автоматически применит оплату. Код можно ввести вручную на другом устройстве.\n\nЭто тестовая оплата: выбор и доплата работают без реальных списаний и передачи NFT. Локальную ссылку открывайте на компьютере, где запущен проект."
            result=Bot(db).request("sendMessage",{"chat_id":db.settings()["owner_chat_id"],"text":text,"link_preview_options":{"is_disabled":True}})
            if not result.get("message_id"):raise RuntimeError("Message delivery was not confirmed; do not blindly repeat")
        print("Test payment created: "+payment["id"]+("; message sent" if args.send else "; not sent"))
    finally:db.engine.dispose()


if __name__=="__main__":
    raise SystemExit("Тестовые оплаты отключены. Создавайте реальные коды в разделе Каталог дашборда.")
