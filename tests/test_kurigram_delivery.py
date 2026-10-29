import asyncio
import base64
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import raw
from pyrogram.storage import Storage

from backend.app.integrations.clients import Mrkt
from backend.app.integrations.kurigram_delivery import KurigramDelivery
from backend.app.safety import IntegrationError, ManualRequired, WriteBlocked
from backend.app.service import Service


@pytest.fixture
def delivery(db, monkeypatch):
    db.configure(delivery_transport="kurigram", transfers_enabled=True)
    adapter = KurigramDelivery(db, Mrkt(db))
    client = SimpleNamespace(
        get_users=AsyncMock(return_value=SimpleNamespace(id=123, is_bot=False, is_deleted=False)),
        resolve_peer=AsyncMock(return_value=raw.types.InputPeerUser(user_id=123, access_hash=7)),
        invoke=AsyncMock(return_value=SimpleNamespace(form_id=42, invoice=SimpleNamespace(
            currency="XTR", prices=[SimpleNamespace(amount=25)]))),
    )
    monkeypatch.setattr(adapter, "telegram", lambda op: asyncio.run(op(client)))
    return adapter, client


@pytest.mark.parametrize("stars", [0, 25])
def test_transfer_exact_gift_and_recipient_without_rpc_retries(delivery, stars):
    adapter, client = delivery
    assert adapter.transfer({"msg_id":5, "transfer_stars":stars}, "buyer", 123) == 123
    calls = client.invoke.call_args_list
    request = calls[-1].args[0]
    if stars:
        assert isinstance(request, raw.functions.payments.SendStarsForm)
        assert request.form_id == 42
        ref, peer = request.invoice.stargift, request.invoice.to_id
        assert len(calls) == 2
    else:
        assert isinstance(request, raw.functions.payments.TransferStarGift)
        ref, peer = request.stargift, request.to_id
        assert len(calls) == 1
    assert ref.msg_id == 5 and peer.user_id == 123
    assert calls[-1].kwargs == {"retries":1, "sleep_threshold":0}


@pytest.mark.parametrize("change", ["recipient", "bot", "deleted", "peer"])
def test_invalid_recipient_never_requests_payment(delivery, change):
    adapter, client = delivery
    user = client.get_users.return_value
    if change == "recipient": user.id = 999
    elif change == "bot": user.is_bot = True
    elif change == "deleted": user.is_deleted = True
    else: client.resolve_peer.return_value = raw.types.InputPeerUser(user_id=999, access_hash=7)
    with pytest.raises(ManualRequired):
        adapter.transfer({"msg_id":5, "transfer_stars":25}, "buyer", 123)
    client.invoke.assert_not_called()


@pytest.mark.parametrize("currency,amount", [("XTR",26), ("USD",25)])
def test_changed_fee_blocks_payment(delivery, currency, amount):
    adapter, client = delivery
    client.invoke.return_value.invoice = SimpleNamespace(currency=currency, prices=[SimpleNamespace(amount=amount)])
    with pytest.raises(ManualRequired, match="Стоимость"):
        adapter.transfer({"msg_id":5, "transfer_stars":25}, "buyer", 123)
    assert client.invoke.call_count == 1


def test_stop_after_quote_blocks_payment(db, delivery):
    adapter, client = delivery
    async def quote(*args, **kwargs):
        db.configure(emergency_stop=True)
        return SimpleNamespace(form_id=42, invoice=SimpleNamespace(currency="XTR", prices=[SimpleNamespace(amount=25)]))
    client.invoke.side_effect = quote
    with pytest.raises(WriteBlocked):
        adapter.transfer({"msg_id":5, "transfer_stars":25}, "buyer", 123)
    assert client.invoke.call_count == 1


def test_session_conversion_uses_current_account_and_keeps_key_in_memory(db, monkeypatch):
    monkeypatch.setattr(db.config, "get", lambda key: "12345" if key == "MRKT_TELEGRAM_API_ID" else None)
    mrkt = Mrkt(db)
    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=123, bot=False)),
                             session=SimpleNamespace(dc_id=2, auth_key=SimpleNamespace(key=b"k"*256)))
    monkeypatch.setattr(mrkt, "telegram", lambda op: asyncio.run(op(client)))
    encoded = KurigramDelivery(db, mrkt).session_string()
    fields = struct.unpack(Storage.SESSION_STRING_FORMAT, base64.urlsafe_b64decode(encoded + "="*(-len(encoded)%4)))
    assert fields == (2,12345,False,b"k"*256,123,False)
    assert not list(db.config.data.glob("*.session"))


def test_selected_transport_routes_to_kurigram(db, monkeypatch):
    db.configure(delivery_transport="kurigram", transfers_enabled=True)
    transfer = Mock(return_value=123)
    monkeypatch.setattr(KurigramDelivery, "transfer", transfer)
    saved = {"msg_id":5, "transfer_stars":25}
    assert Mrkt(db).transfer(saved, "buyer", 123) == 123
    transfer.assert_called_once_with(saved, "buyer", 123)


@pytest.mark.parametrize("authorized", [True, False])
def test_connection_uses_memory_session_proxy_and_disconnects(db, monkeypatch, authorized):
    import pyrogram
    adapter = KurigramDelivery(db, Mrkt(db))
    monkeypatch.setattr(adapter, "session_string", lambda: "in-memory-session")
    values = {"MRKT_TELEGRAM_API_ID":"12345", "MRKT_TELEGRAM_API_HASH":"test-hash",
              "MRKT_MTPROTO_PROXY":"tg://proxy?server=localhost&port=443&secret="+"a"*32}
    monkeypatch.setattr(db.config, "get", lambda key: values.get(key))
    client = SimpleNamespace(connect=AsyncMock(return_value=authorized), is_connected=True,
                             disconnect=AsyncMock())
    factory = Mock(return_value=client)
    monkeypatch.setattr(pyrogram, "Client", factory)
    operation = AsyncMock(return_value=123)
    if authorized:
        assert adapter.telegram(operation) == 123
        operation.assert_awaited_once_with(client)
    else:
        with pytest.raises(IntegrationError, match="авторизована"):
            adapter.telegram(operation)
        operation.assert_not_called()
    client.disconnect.assert_awaited_once()
    assert factory.call_args.kwargs["in_memory"] is True
    assert factory.call_args.kwargs["session_string"] == "in-memory-session"
    assert factory.call_args.kwargs["proxy"] == values["MRKT_MTPROTO_PROXY"]


def test_uncertain_transfer_is_not_retried_or_marked_sent(db, monkeypatch):
    db.configure(delivery_transport="kurigram", transfers_enabled=True, delivery_tested=True)
    db.put("lot", "l", {"pair_id":"p"})
    db.put("order", "o", {"lot_id":"l", "stage":"WITHDRAWN"})
    db.put("operation", "o:transfer", {"state":"QUEUED", "action":"order.transfer", "order_id":"o",
        "payload":{"saved":{"msg_id":5,"transfer_stars":25},"recipient":"buyer","recipient_id":123},
        "expected":{}})
    svc = Service(db)
    monkeypatch.setattr(svc, "order_preflight", lambda op: None)
    transfer = Mock(side_effect=TimeoutError("Uncertain response"))
    monkeypatch.setattr(KurigramDelivery, "transfer", transfer)
    svc.execute("o:transfer")
    assert db.get("operation", "o:transfer")["state"] == "UNKNOWN"
    svc.execute("o:transfer")
    assert transfer.call_count == 1
    assert db.get("order", "o")["stage"] != "COMPLETE"
    assert db.list("ledger") == []
