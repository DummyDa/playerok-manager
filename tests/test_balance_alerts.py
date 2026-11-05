from unittest.mock import Mock
import pytest
from backend.app import balance_alerts as alerts
from backend.app.service import Service
from backend.app.db import Task
from backend.app.owner_notifications import send_pending


@pytest.mark.parametrize("ton,stars,expected",[("15",51,0),("14.99",51,1),("15",50,1),("15",49,1),("14",50,1)])
def test_low_balance_boundaries_and_no_spam(db,ton,stars,expected):
    db.configure(notifications_enabled=True,owner_chat_id=77)
    for _ in range(3):alerts.observe(db,{"mrkt_ton":ton,"stars":stars})
    assert len(db.list("notification"))==expected
    if expected:
        bot=Mock();bot.request.return_value={"message_id":1}
        send_pending(db,bot)
        bot.request.assert_called_once()
        assert "баланс" in bot.request.call_args.args[1]["text"]


def test_balance_alerts_rearm_only_after_recovery_and_persist(db):
    db.configure(notifications_enabled=True)
    alerts.observe(db,{"mrkt_ton":"14","stars":49})
    alerts.observe(db,{"mrkt_ton":"10","stars":30})
    assert len(db.list("notification"))==1
    alerts.observe(db,{"mrkt_ton":"20","stars":30})
    alerts.observe(db,{"mrkt_ton":"14","stars":30})
    assert len(db.list("notification"))==2
    alerts.observe(db,{"mrkt_ton":"14","stars":100})
    alerts.observe(db,{"mrkt_ton":"14","stars":50})
    assert len(db.list("notification"))==3


def waiting(db):
    db.configure(notifications_enabled=True,owner_chat_id=77)
    order=db.put("order","o",{"stage":"PURCHASING","lot_id":"l","title":"Gift <b>","catalog_credit_id":"c","catalog_revision":1,"snapshot":{"quote_ton":"4"}})
    db.put("catalog_credit_payment","c",{"state":"EXECUTING","fulfillment_order_id":"o","revision":1,"reservation_rub":"500"})
    op=db.put("operation","o:buy",{"order_id":"o","state":"QUEUED","action":"order.buy"})
    alerts.wait_for_balance(db,op)
    return db.get("order","o")["balance_wait_token"]


def test_wait_keeps_reservation_and_repeat_is_idempotent(db):
    token=waiting(db)
    alerts.wait_for_balance(db,db.get("operation","o:buy"))
    assert db.get("order","o")["stage"]=="WAITING_BALANCE"
    assert db.get("catalog_credit_payment","c")["reservation_rub"]=="500"
    assert len(db.list("notification"))==1
    assert not db.list("catalog_message")
    alerts.retry(db,token)
    assert db.get("order","o")["stage"]=="DISCOVERED"
    assert db.get("operation","o:buy")["state"]=="RETRY_PENDING"
    with pytest.raises(ValueError):alerts.retry(db,token)
    with db.sessions() as s:assert len(list(s.query(Task).filter_by(kind="process_order")))==1


@pytest.mark.parametrize("state,submitted",[("UNKNOWN",None),("SUBMITTED",123),("FAILED",123),("COMPLETE",123),("VERIFYING",123)])
def test_retry_blocks_uncertain_or_completed_purchase(db,state,submitted):
    token=waiting(db)
    db.put("operation","o:buy",{"state":state,"submitted_at":submitted})
    with pytest.raises(ValueError):alerts.retry(db,token)
    assert db.get("order","o")["stage"]=="WAITING_BALANCE"


def test_callback_checks_owner_message_and_single_use(db):
    token=waiting(db);bot=Mock();bot.request.return_value={"message_id":88}
    send_pending(db,bot)
    callback={"id":"cb","data":"mb:"+token,"from":{"id":12},"message":{"message_id":88,"chat":{"id":77,"type":"private"}}}
    alerts.handle(Service(db),bot,callback)
    assert db.get("order","o")["stage"]=="WAITING_BALANCE"
    callback["from"]["id"]=77;callback["message"]["message_id"]=89
    alerts.handle(Service(db),bot,callback)
    assert db.get("order","o")["stage"]=="WAITING_BALANCE"
    callback["message"]["message_id"]=88
    alerts.handle(Service(db),bot,callback);alerts.handle(Service(db),bot,callback)
    assert db.get("order","o")["stage"]=="DISCOVERED"


def test_wait_is_never_allowed_after_submission(db):
    db.put("order","o",{"stage":"PURCHASING"})
    op=db.put("operation","o:buy",{"order_id":"o","state":"SUBMITTED","submitted_at":123,"action":"order.buy"})
    with pytest.raises(ValueError):alerts.wait_for_balance(db,op)
    assert not db.list("notification")
