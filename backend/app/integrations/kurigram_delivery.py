"""Gift delivery using Kurigram and an in-memory copy of the saved user session."""
from __future__ import annotations

import asyncio
import base64
import struct
from importlib.metadata import PackageNotFoundError, version

from ..safety import IntegrationError, ManualRequired, gate


def require_kurigram():
    try:
        version("kurigram")
    except PackageNotFoundError:
        raise IntegrationError("Установите зависимости проекта: для этого способа выдачи нужен Kurigram") from None


class KurigramDelivery:
    def __init__(self, db, mrkt):
        self.db, self.mrkt = db, mrkt

    def session_string(self):
        # Both string and legacy SQLite Telethon sessions are supported. The
        # existing client disconnects before Kurigram uses the authorization key.
        async def export(client):
            me = await client.get_me()
            if not me or me.bot or getattr(me, "deleted", False):
                raise IntegrationError("Для Kurigram нужна сессия личного Telegram-аккаунта")
            from pyrogram.storage import Storage
            session = client.session
            packed = struct.pack(Storage.SESSION_STRING_FORMAT, session.dc_id,
                                 int(self.db.config.get("MRKT_TELEGRAM_API_ID")), False,
                                 session.auth_key.key, me.id, False)
            return base64.urlsafe_b64encode(packed).decode().rstrip("=")
        return self.mrkt.telegram(export)

    def telegram(self, operation):
        require_kurigram()
        session = self.session_string()

        async def run():
            from pyrogram import Client
            config = self.db.config
            client = Client("playerok2-delivery", api_id=int(config.get("MRKT_TELEGRAM_API_ID")),
                            api_hash=config.get("MRKT_TELEGRAM_API_HASH"), session_string=session,
                            in_memory=True, no_updates=True, sleep_threshold=0,
                            proxy=config.get("MRKT_MTPROTO_PROXY") or None)
            try:
                async with asyncio.timeout(30):
                    # connect(), unlike start(), never asks for a phone/code.
                    if not await client.connect():
                        raise IntegrationError("Telegram-сессия больше не авторизована")
                    return await operation(client)
            except TimeoutError:
                raise IntegrationError("Kurigram: Telegram не ответил за 30 секунд; требуется сверка") from None
            finally:
                if client.is_connected:
                    await client.disconnect()
        return asyncio.run(run())

    def transfer(self, saved, recipient, expected_recipient_id):
        gate(self.db, "transfers")

        async def send(client):
            from pyrogram import raw
            user = await client.get_users(recipient)
            if not user or user.is_bot or user.is_deleted:
                raise ManualRequired("Получатель не является активным пользователем")
            if user.id != expected_recipient_id:
                raise ManualRequired("Изменился ID Telegram получателя")
            peer = await client.resolve_peer(user.id)
            if not isinstance(peer, raw.types.InputPeerUser) or peer.user_id != expected_recipient_id:
                raise ManualRequired("Изменился ID Telegram получателя")
            ref = raw.types.InputSavedStarGiftUser(msg_id=saved["msg_id"])
            # Raw calls keep fee checks under our control. transfer_gift() can
            # automatically pay a newly requested fee without an application cap.
            if saved["transfer_stars"]:
                invoice = raw.types.InputInvoiceStarGiftTransfer(stargift=ref, to_id=peer)
                form = await client.invoke(raw.functions.payments.GetPaymentForm(invoice=invoice), retries=1)
                if form.invoice.currency != "XTR" or sum(p.amount for p in form.invoice.prices) != saved["transfer_stars"]:
                    raise ManualRequired("Стоимость передачи изменилась")
                request = raw.functions.payments.SendStarsForm(form_id=form.form_id, invoice=invoice)
            else:
                request = raw.functions.payments.TransferStarGift(stargift=ref, to_id=peer)
            gate(self.db, "transfers")
            # Kurigram counts attempts, so 1 means one send, without RPC retries.
            await client.invoke(request, retries=1, sleep_threshold=0)
            return user.id

        return self.telegram(send)
