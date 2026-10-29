from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from backend.app import buyer_messages as replies
from backend.app.operations import recover
from backend.app.service import Service
from backend.app.schemas import Settings


@pytest.fixture
def messaging(db,monkeypatch):
    db.configure(buyer_paid_enabled=True,buyer_delivered_enabled=True,
                 buyer_paid_template="Оплата {price} ₽ за {title}, {recipient}",
                 buyer_delivered_template="Передан {gift}: {gift_link}")
    order=db.put("order","o",{"stage":"TRANSFERRED","chat_id":"deal-chat","sale_rub":"565",
                             "title":"Chill Flame","recipient":"buyer","gift_slug":"ChillFlame-306647"})
    api=NS(orders=Mock(return_value=[{"id":"o","status":"PAID","chat_id":"deal-chat"}]),
           send_chat_message=Mock(return_value={"id":"remote-message"}))
    monkeypatch.setattr("backend.app.integrations.clients.Playerok",lambda _:api)
    return order,api,NS(db=db,delivery_confirmed=Mock(return_value=True))


def test_templates_render_only_known_fields_and_allow_literal_braces():
    order={"id":"o","recipient":"@buyer","gift_slug":"Gift-1","sale_rub":"565","title":"Lot"}
    assert replies.render_template("{{текст}} {recipient} {gift_link} {order_id}",order)=="{текст} @buyer https://t.me/nft/Gift-1 o"
    for text in ["{unknown}","{title.__class__}","{title[0]}","{title!r}","{price:100}","{"]:
        with pytest.raises(ValueError):Settings(buyer_paid_template=text)


def test_duplicate_event_and_repeated_task_send_only_one_message(db,messaging):
    order,api,service=messaging
    replies.queue(db,order,"paid")
    replies.queue(db,order,"paid")
    assert len(db.list("buyer_message"))==1
    replies.send(service,"o:paid")
    replies.send(service,"o:paid")
    api.send_chat_message.assert_called_once_with("deal-chat","Оплата 565 ₽ за Chill Flame, @buyer")
    assert db.get("buyer_message","o:paid")["state"]=="SENT"
    assert db.get("order","o")["stage"]=="TRANSFERRED"


def test_uncertain_send_is_never_retried_and_does_not_stop_order(db,messaging):
    order,api,service=messaging
    replies.queue(db,order,"paid")
    api.send_chat_message.side_effect=TimeoutError("lost response")
    with pytest.raises(TimeoutError):replies.send(service,"o:paid")
    assert db.get("buyer_message","o:paid")["state"]=="UNKNOWN"
    replies.send(service,"o:paid")
    assert api.send_chat_message.call_count==1
    assert db.get("order","o")["stage"]=="TRANSFERRED"


def test_delivered_reply_requires_verified_transfer(db,messaging):
    order,api,service=messaging
    replies.queue(db,order,"delivered")
    service.delivery_confirmed.return_value=False
    with pytest.raises(replies.IntegrationError,match="не подтверждена"):
        replies.send(service,"o:delivered")
    api.send_chat_message.assert_not_called()
    assert db.get("buyer_message","o:delivered")["state"]=="FAILED"


def test_confirmed_delivery_reply_contains_exact_gift_link(db,messaging):
    order,api,service=messaging
    replies.queue(db,order,"delivered")
    replies.send(service,"o:delivered")
    api.send_chat_message.assert_called_once_with("deal-chat","Передан ChillFlame-306647: https://t.me/nft/ChillFlame-306647")


def test_recovery_does_not_repeat_submitted_reply(db,messaging):
    order,api,service=messaging
    replies.queue(db,order,"paid")
    db.put("buyer_message","o:paid",{"state":"SUBMITTED"})
    recover(db)
    replies.send(service,"o:paid")
    assert db.get("buyer_message","o:paid")["state"]=="UNKNOWN"
    api.send_chat_message.assert_not_called()


@pytest.mark.parametrize("change",["disabled","refunded","wrong_chat"])
def test_send_rechecks_permissions_payment_and_chat(db,messaging,change):
    order,api,service=messaging
    replies.queue(db,order,"paid")
    if change=="disabled":db.configure(buyer_paid_enabled=False)
    elif change=="refunded":api.orders.return_value[0]["status"]="REFUNDED"
    else:api.orders.return_value[0]["chat_id"]="different-chat"
    if change=="wrong_chat":
        with pytest.raises(replies.IntegrationError):replies.send(service,"o:paid")
    else:replies.send(service,"o:paid")
    api.send_chat_message.assert_not_called()


def test_paid_reply_is_queued_only_for_new_orders_after_baseline(db,monkeypatch):
    db.configure(buyer_paid_enabled=True)
    records=[{"id":"old","status":"PAID","title":"Lot","lot_id":"l","recipient":"buyer","sale_rub":"565","chat_id":"c1"}]
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:NS(orders=lambda:records))
    svc=Service(db)
    svc.orders()
    assert db.list("buyer_message")==[]
    records.append({**records[0],"id":"new","chat_id":"c2"})
    svc.orders()
    svc.orders()
    assert [m["id"] for m in db.list("buyer_message")]==["new:paid"]


def test_template_disabled_during_chat_check_is_not_sent(db,messaging):
    order,api,service=messaging
    replies.queue(db,order,"paid")
    def read(**kwargs):
        db.configure(buyer_paid_enabled=False)
        return [{"id":"o","status":"PAID","chat_id":"deal-chat"}]
    api.orders.side_effect=read
    replies.send(service,"o:paid")
    api.send_chat_message.assert_not_called()


def test_message_adapter_uses_single_graphql_call(db,monkeypatch):
    from backend.app.integrations.clients import Playerok
    db.secret("playerok_cookies","test-cookie-value")
    api=Playerok(db)
    post=Mock(return_value={"createChatMessage":{"id":"message"}})
    monkeypatch.setattr(api.client,"_graphql_post_json",post)
    assert api.send_chat_message("deal-chat","Спасибо!")=={"id":"message"}
    post.assert_called_once()
    assert post.call_args.args[0]["variables"]["input"]=={"chatId":"deal-chat","imagesIds":[],"text":"Спасибо!"}


@pytest.mark.parametrize("confirmed",[False,True])
def test_service_queues_delivered_reply_only_after_confirmation(db,monkeypatch,confirmed):
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True,buyer_delivered_enabled=True)
    db.put("lot","l",{"pair_id":"p"})
    db.put("order","o",{"stage":"TRANSFERRED","lot_id":"l","gift_slug":"Gift-1","snapshot":{},
                         "recipient_id":123,"recipient":"buyer","chat_id":"chat"})
    service=Service(db)
    monkeypatch.setattr(service,"delivery_confirmed",lambda order:confirmed)
    if not confirmed:
        with pytest.raises(replies.IntegrationError):service.process_order("o")
        assert db.list("buyer_message")==[]
    else:
        service.process_order("o")
        service.process_order("o")
        assert len(db.list("buyer_message"))==1
        assert db.get("buyer_message","o:delivered")["state"]=="QUEUED"
