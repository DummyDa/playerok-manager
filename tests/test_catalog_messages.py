import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app import catalog_credit as credit,catalog_messages as messages
from backend.app.main import create_app
from backend.app.service import Service
from backend.app.operations import recover


@pytest.fixture
def paid(db):
    db.configure(catalog_promo_started_at=time.time()-10,orders_baselined=True,catalog_public_url="http://127.0.0.1:3180",buyer_paid_enabled=False,buyer_delivered_enabled=False)
    db.put("pair","pair",{"collection":"Liberty Figure","model":"Obsidian"})
    db.put("lot","ad",{"pair_id":"pair","delivery_mode":"telegram_nft","lot_type":"PROMOTIONAL"})
    return {"id":"paid-ad","status":"PAID","sale_rub":"800","lot_id":"ad","recipient":"buyer","buyer_id":"payer","chat_id":"chat","created_at":time.time(),"title":"Ad"}


def body(db,message):return db.cipher.decrypt(message["encrypted_text"].encode()).decode()


def test_selected_gift_sends_editable_waiting_reply_once_after_full_payment(db,paid):
    db.configure(manual_gift_delivery=True,catalog_selected_template="Выбран {gift} для {recipient}. {delivery_notice}")
    credit.handle_payment(Service(db),paid)
    wallet=db.list("catalog_credit_payment")[0]
    db.put("catalog_credit_payment",wallet["id"],{"state":"AWAITING_TOPUP","selected":{"id":"gift","slug":"Gift-1"},"revision":1})
    messages.selection_accepted(db,db.get("catalog_credit_payment",wallet["id"]))
    assert not any(m["event"].startswith("selected:") for m in db.list("catalog_message"))
    db.put("catalog_credit_payment",wallet["id"],{"state":"EXECUTING"})
    current=db.get("catalog_credit_payment",wallet["id"])
    messages.selection_accepted(db,current);messages.selection_accepted(db,current)
    selected=[m for m in db.list("catalog_message") if m["event"]=="selected:1"]
    assert len(selected)==1
    assert body(db,selected[0]).startswith("Выбран Gift-1 для @buyer. 🎁 Подарок передаст продавец")


def test_catalog_delivery_uses_editable_shared_template_even_in_manual_mode(db,paid):
    db.configure(manual_gift_delivery=True,buyer_delivered_template="Мой ответ: {gift} для {recipient}. {gift_link}")
    credit.handle_payment(Service(db),paid)
    wallet=db.list("catalog_credit_payment")[0]
    messages.gift_delivered(db,wallet,{"id":"delivery","gift_slug":"Gift-1","recipient":"buyer","catalog_revision":1})
    record=next(m for m in db.list("catalog_message") if m["event"]=="delivered:1")
    assert body(db,record).startswith("Мой ответ: Gift-1 для @buyer. https://t.me/nft/Gift-1")


def test_paid_promotion_notifies_owner_once_before_purchase(db,paid,monkeypatch):
    db.configure(notifications_enabled=True,owner_chat_id=77)
    remote=Mock();remote.orders.return_value=[paid]
    monkeypatch.setattr("backend.app.service.Playerok",lambda db:remote)
    service=Service(db);service.orders();service.orders()
    notices=[n for n in db.list("notification") if n.get("event")=="paid"]
    assert len(notices)==1 and "Заказ оплачен" in notices[0]["payload"]["text"]
    assert "800 ₽" in notices[0]["payload"]["text"]


def test_promotional_payment_creates_one_code_and_instructions_without_issuing_gift(db,paid):
    service=Service(db)
    assert credit.handle_payment(service,paid)
    assert credit.handle_payment(service,paid)
    wallets=db.list("catalog_credit_payment")
    assert len(wallets)==1 and credit.funds(wallets[0])==800
    assert wallets[0]["buyer_id"]=="payer"
    assert db.get("order","paid-ad")["catalog_root_credit_id"]==wallets[0]["id"]
    assert db.list("operation")==[]
    queue=[m for m in db.list("catalog_message") if not m.get("after_message_id")];assert len(queue)==1
    link=next(m for m in db.list("catalog_message") if m.get("after_message_id"))
    assert body(db,link).startswith("http://127.0.0.1:3180/") and "\n" not in body(db,link)
    assert body(db,queue[0]).endswith("Выберите подарок в каталоге:")
    assert "800 ₽" in body(db,queue[0]) and "collection=Liberty+Figure#code=" in body(db,link)
    assert "encrypted_text" in queue[0] and "text" not in queue[0]
    assert "Никаких оплат на сайте нет" in body(db,queue[0])
    assert "!продавец" in body(db,queue[0])
    assert db.list("buyer_message")==[]
    with pytest.raises(Exception,match="каталога"):service.process_order("paid-ad")


def test_catalog_message_has_single_attempt_and_unknown_is_not_replayed(db,paid,monkeypatch):
    credit.handle_payment(Service(db),paid)
    client=Mock();client.orders.return_value=[paid];client.send_chat_message.side_effect=TimeoutError()
    monkeypatch.setattr("backend.app.integrations.clients.Playerok",lambda _:client)
    key=next(m["id"] for m in db.list("catalog_message") if not m.get("after_message_id"))
    messages.send(NS(db=db),key);messages.send(NS(db=db),key)
    assert db.get("catalog_message",key)["state"]=="UNKNOWN"
    recover(db);messages.tick(db);messages.send(NS(db=db),key)
    client.send_chat_message.assert_called_once()


def test_wrong_chat_cannot_receive_code(db,paid,monkeypatch):
    credit.handle_payment(Service(db),paid)
    client=Mock();client.orders.return_value=[{**paid,"chat_id":"foreign"}]
    monkeypatch.setattr("backend.app.integrations.clients.Playerok",lambda _:client)
    with pytest.raises(ValueError,match="Чат"):messages.send(NS(db=db),next(m["id"] for m in db.list("catalog_message") if not m.get("after_message_id")))
    client.send_chat_message.assert_not_called()


def test_existing_paid_and_historical_orders_are_not_migrated(db,paid):
    service=Service(db)
    assert not credit.handle_payment(service,{**paid,"created_at":time.time()-100})
    db.put("order",paid["id"],{"stage":"PURCHASING","status":"PAID"})
    assert not credit.handle_payment(service,paid)
    assert db.list("catalog_credit_payment")==[] and db.list("catalog_message")==[]


def test_ordinary_manual_and_temporary_lots_keep_previous_path(db,paid):
    for values in [{"delivery_mode":"manual"},{"catalog_gift_id":"g"},{"lot_type":"TEMPORARY"}]:
        db.put("lot","ad",{**{"pair_id":"pair","delivery_mode":"telegram_nft","lot_type":"PROMOTIONAL"},**values},merge=False)
        assert not credit.handle_payment(Service(db),paid)
    assert db.list("catalog_message")==[]


def test_missing_url_waits_for_configuration_without_sending_invalid_link(db,paid):
    db.configure(catalog_public_url="")
    credit.handle_payment(Service(db),paid)
    queued=db.list("catalog_message")
    assert queued[0]["event"]=="promo_waiting_url"
    assert "127.0.0.1" not in body(db,queued[0])
    db.configure(catalog_public_url="http://127.0.0.1:3180")
    messages.tick(db);messages.tick(db)
    assert len(db.list("catalog_message"))==3
    assert any(m["event"]=="promo_paid" for m in db.list("catalog_message"))


def test_topup_link_and_ack_are_separate_and_delivery_waits_for_proof(db,paid):
    credit.handle_payment(Service(db),paid)
    wallet=db.list("catalog_credit_payment")[0]
    db.put("catalog_credit_payment",wallet["id"],{"revision":1,"recipient":"buyer","state":"AWAITING_TOPUP"})
    wallet=db.get("catalog_credit_payment",wallet["id"])
    messages.topup_requested(db,wallet,{"url":"https://playerok.com/products/topup","topup_rub":"200"})
    receipt={"id":"topup","credit_id":wallet["id"],"amount_rub":"200","chat_id":"chat","buyer_id":"payer","accepted":True,"revision":1}
    messages.topup_paid(db,{**wallet,"state":"EXECUTING"},receipt)
    texts=[body(db,m) for m in db.list("catalog_message")]
    assert any("https://playerok.com/products/topup" in t for t in texts)
    assert any("Доплата получена: 200 ₽" in t for t in texts)
    assert not any("Подарок передан" in t for t in texts)
    messages.gift_delivered(db,wallet,{"gift_slug":"Gift-1","recipient":"buyer","catalog_revision":1})
    assert sum("Подарок передан" in body(db,m) for m in db.list("catalog_message"))==1
    delivered=next(body(db,m) for m in db.list("catalog_message") if m["event"].startswith("delivered:"))
    assert "подтвердите получение" in delivered and "оставьте отзыв" in delivered


def test_unpaid_promotion_no_code_and_missing_buyer_requires_manual_review(db,paid):
    assert not credit.handle_payment(Service(db),{**paid,"status":"PENDING"})
    assert credit.handle_payment(Service(db),{**paid,"buyer_id":None})
    assert db.get("order",paid["id"])["stage"]=="MANUAL"
    assert db.list("catalog_credit_payment")==[]


def test_message_templates_and_local_url_can_be_saved(db):
    with TestClient(create_app(db)) as client:
        auth=client.post("/api/v1/auth/setup",json={"password":"a-long-test-password"})
        headers={"X-CSRF-Token":auth.json()["csrf"]}
        payload={"catalog_public_url":"http://127.0.0.1:3180","catalog_promo_paid_template":"Оплачено {amount}. {url} Код {code}","catalog_topup_paid_template":"Доплата {amount} ₽ принята"}
        assert client.patch("/api/v1/settings",headers=headers,json=payload).status_code==200
        assert client.patch("/api/v1/settings",headers=headers,json={"catalog_promo_paid_template":"{code.secret}"}).status_code==422
        assert client.patch("/api/v1/settings",headers=headers,json={"catalog_public_url":"javascript:alert(1)"}).status_code==422
