import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from pyrogram import raw

from scripts import test_gift_delivery as script


def gift(slug, msg_id, *, owner=10, **changes):
    nft = raw.types.StarGiftUnique(id=msg_id, gift_id=1, title="Gift", slug=slug, num=msg_id,
        attributes=[], availability_issued=100, availability_total=100, owner_id=raw.types.PeerUser(user_id=owner))
    return raw.types.SavedStarGift(date=1, gift=nft, msg_id=msg_id, transfer_stars=0, **changes)


def test_gift_list_excludes_reserved_locked_and_other_owners(db):
    db.put("order", "o", {"gift_slug":"Reserved-1"})
    items = [gift("Reserved-1",1), gift("Locked-2",2,can_transfer_at=9999999999),
             gift("Other-3",3,owner=999), gift("Available-4",4), gift("Sale-5",5)]
    items[-1].gift.resell_amount = [raw.types.StarsAmount(amount=1, nanos=0)]
    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=10)),
        get_users=AsyncMock(return_value=SimpleNamespace(id=20,is_bot=False,is_deleted=False,first_name="Buyer",last_name=None)),
        invoke=AsyncMock(return_value=SimpleNamespace(gifts=items,next_offset=None)))
    adapter = SimpleNamespace(telegram=lambda op: asyncio.run(op(client)))
    plan = script.prepare(db, adapter, "buyer")
    assert plan["gifts"][0]["slug"] == "Available-4"
    assert plan["count"] == 1 and plan["recipient_id"] == 20
    assert len(plan["gifts"]) == 1


@pytest.fixture
def live_test(db, monkeypatch):
    monkeypatch.setattr(script, "has_user_session", lambda db: True)
    mrkt = Mock()
    mrkt.saved.return_value = {"msg_id":5,"slug":"Gift-5","transfer_stars":25,"can_transfer_at":0}
    mrkt.owner.return_value = 20
    mrkt.transfer_form.return_value = {"recipient_id":20,"currency":"XTR","stars":25}
    mrkt.telegram.return_value = 10
    mrkt.transfer_receipt.return_value = None
    adapter = Mock()
    monkeypatch.setattr(script, "Mrkt", lambda db: mrkt)
    monkeypatch.setattr(script, "KurigramDelivery", lambda db, client: adapter)
    monkeypatch.setattr(script, "prepare", lambda *args: {
        "gifts":[dict(mrkt.saved.return_value)], "recipient_id":20, "recipient_name":"Buyer", "sender_id":10,"count":3})
    return mrkt, adapter


def test_cancel_never_sends_or_changes_settings(db, live_test):
    mrkt, adapter = live_test
    before = db.settings()
    assert script.run_test(db, "buyer", ask=lambda _:"", output=lambda _:None) is False
    adapter.transfer.assert_not_called()
    assert db.list("delivery_test") == [] and db.settings() == before


def test_confirm_sends_once_and_verifies_owner(db, live_test):
    mrkt, adapter = live_test
    before = db.settings()
    assert script.run_test(db, "buyer", ask=lambda prompt:"1" if prompt.startswith("Номер") else "ОТПРАВИТЬ", output=lambda _:None)
    adapter.transfer.assert_called_once_with(mrkt.saved.return_value, "buyer", 20)
    mrkt.owner.assert_called_once_with("Gift-5")
    assert db.list("delivery_test")[0]["state"] == "COMPLETE"
    assert db.settings() == before and db.list("order") == []


def test_ambiguous_failure_persists_and_blocks_another_transfer(db, live_test):
    mrkt, adapter = live_test
    adapter.transfer.side_effect = TimeoutError("Response lost")
    mrkt.owner.return_value = 10
    with pytest.raises(TimeoutError):
        script.run_test(db, "buyer", ask=lambda prompt:"1" if prompt.startswith("Номер") else "ОТПРАВИТЬ", output=lambda _:None)
    assert db.list("delivery_test")[0]["state"] == "UNKNOWN"
    with pytest.raises(script.ManualRequired, match="сверки"):
        script.run_test(db, "buyer", ask=lambda prompt:"1" if prompt.startswith("Номер") else "ОТПРАВИТЬ", output=lambda _:None)
    assert adapter.transfer.call_count == 1


def test_fee_changed_after_confirmation_prevents_send(db, live_test, monkeypatch):
    mrkt, adapter = live_test
    def confirm(prompt):
        if prompt.startswith("Номер"):
            return "1"
        mrkt.saved.return_value["transfer_stars"] = 26
        return "ОТПРАВИТЬ"
    with pytest.raises(script.ManualRequired, match="изменились"):
        script.run_test(db, "buyer", ask=confirm, output=lambda _:None)
    adapter.transfer.assert_not_called()
    assert db.list("delivery_test") == []


def test_manual_context_only_grants_local_transfer_permission(db):
    ctx = script.ManualTestContext(db)
    assert ctx.settings()["transfers_enabled"] is True
    assert db.settings()["transfers_enabled"] is False
    db.configure(emergency_stop=True)
    assert ctx.settings()["emergency_stop"] is True


def test_old_unknown_still_owned_can_run_new_test_without_auto_send(db, live_test):
    mrkt, adapter = live_test
    mrkt.owner.return_value = 10
    db.put("delivery_test", "old", {"state":"UNKNOWN", "slug":"Gift-5", "recipient_id":20,
        "submitted_at":time.time()-180, "sender_id":10, "msg_id":5})
    assert script.run_test(db, "buyer", ask=lambda _:"", output=lambda _:None) is False
    assert db.get("delivery_test", "old")["state"] == "NOT_EXECUTED"
    adapter.transfer.assert_not_called()


@pytest.mark.parametrize("owner,present,msg_id", [(None,True,5),(999,True,5),(10,False,5),(10,True,6)])
def test_inconclusive_reconciliation_keeps_block(db, live_test, owner, present, msg_id):
    mrkt, adapter = live_test
    mrkt.owner.return_value = owner
    if not present:
        mrkt.saved.return_value = None
    else:
        mrkt.saved.return_value["msg_id"] = msg_id
    db.put("delivery_test", "old", {"state":"UNKNOWN", "slug":"Gift-5", "recipient_id":20,
        "submitted_at":time.time()-180, "msg_id":5})
    with pytest.raises(script.ManualRequired, match="сверки"):
        script.reconcile_tests(db, mrkt, output=lambda _:None)
    assert db.get("delivery_test", "old")["state"] == "UNKNOWN"
    adapter.transfer.assert_not_called()


def test_legacy_unknown_without_sender_metadata_can_reconcile(db, live_test):
    mrkt, adapter = live_test
    mrkt.owner.return_value = 10
    record = {"id":"old", "state":"UNKNOWN", "slug":"Gift-5", "recipient_id":20,
        "updated_at":time.time()-180}
    db.list = Mock(return_value=[record])
    script.reconcile_tests(db, mrkt, output=lambda _:None)
    assert db.get("delivery_test", "old")["state"] == "NOT_EXECUTED"
    adapter.transfer.assert_not_called()


@pytest.mark.parametrize("value", ["@buyer_name", "buyer_name"])
def test_username(value):
    assert script.username(value) == "buyer_name"


def test_number_selection_reprompts_for_invalid_values_and_selects_exact_gift():
    gifts = [{"slug":"First-1", "transfer_stars":0}, {"slug":"Second-2", "transfer_stars":25}]
    answers = iter(["abc", "0", "-1", "3", "2"])
    lines = []
    chosen = script.select_gift(gifts, ask=lambda _:next(answers), output=lines.append)
    assert chosen == gifts[1] and chosen is not gifts[1]
    assert sum("Введите число" in line for line in lines) == 4
    assert "2. Second-2" in lines[2]


def test_selected_second_gift_is_the_one_sent(db, live_test, monkeypatch):
    mrkt, adapter = live_test
    first = {"msg_id":1,"slug":"First-1","transfer_stars":0,"can_transfer_at":0}
    second = {"msg_id":2,"slug":"Second-2","transfer_stars":0,"can_transfer_at":0}
    monkeypatch.setattr(script, "prepare", lambda *args: {
        "gifts":[first,second], "sender_id":10,"recipient_id":20,"recipient_name":"Buyer"})
    mrkt.saved.return_value = dict(second)
    answers = iter(["2", "ОТПРАВИТЬ"])
    assert script.run_test(db, "buyer", ask=lambda _:next(answers), output=lambda _:None)
    adapter.transfer.assert_called_once_with(second, "buyer", 20)
    mrkt.saved.assert_called_once_with("Second-2")
    assert db.list("delivery_test")[0]["slug"] == "Second-2"
    mrkt.transfer_form.assert_not_called()


def test_cancel_after_number_selection_never_sends(db, live_test):
    mrkt, adapter = live_test
    answers = iter(["1", ""])
    assert script.run_test(db, "buyer", ask=lambda _:next(answers), output=lambda _:None) is False
    adapter.transfer.assert_not_called()
    assert db.list("delivery_test") == []


def test_hidden_owner_uses_exact_telegram_receipt(db, live_test):
    mrkt, adapter = live_test
    mrkt.owner.return_value = None
    receipt = {"message_id":100, "recipient_id":20, "slug":"Gift-5"}
    mrkt.transfer_receipt.return_value = receipt
    answers = iter(["1", "ОТПРАВИТЬ"])
    assert script.run_test(db, "buyer", ask=lambda _:next(answers), output=lambda _:None)
    record = db.list("delivery_test")[0]
    assert record["state"] == "COMPLETE"
    assert record["verification"] == "telegram_service_message" and record["receipt"] == receipt
    mrkt.transfer_receipt.assert_called_once_with("Gift-5", "buyer",20,record["submitted_at"],5)


def test_hidden_owner_without_receipt_stays_unknown(db, live_test):
    mrkt, adapter = live_test
    mrkt.owner.return_value = None
    answers = iter(["1", "ОТПРАВИТЬ"])
    with pytest.raises(script.ManualRequired, match="служебного"):
        script.run_test(db, "buyer", ask=lambda _:next(answers), output=lambda _:None)
    assert db.list("delivery_test")[0]["state"] == "UNKNOWN"
    adapter.transfer.assert_called_once()


def test_previous_hidden_owner_reconciles_by_receipt_without_sending(db, live_test):
    mrkt, adapter = live_test
    mrkt.owner.return_value = None
    mrkt.transfer_receipt.return_value = {"message_id":100,"recipient_id":20,"slug":"Gift-5"}
    db.put("delivery_test", "old", {"state":"UNKNOWN","slug":"Gift-5","recipient_id":20,
        "recipient":"buyer","submitted_at":time.time()-180,"msg_id":5})
    script.reconcile_tests(db, mrkt, output=lambda _:None)
    assert db.get("delivery_test", "old")["state"] == "COMPLETE"
    adapter.transfer.assert_not_called()
