from datetime import datetime,timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time

from backend.app.buyer_support import poll


def test_support_command_notifies_owner_once_and_checks_buyer(db,monkeypatch):
    db.put("support_cursor","main",{"started_at":time.time()-10,"offset":0})
    db.put("order","deal",{"chat_id":"chat","buyer_id":"buyer"})
    def message(id,user):return NS(id=id,user=NS(id=user),text="!продавец",created_at=datetime.now(timezone.utc))
    client=Mock()
    client.account.return_value.get_chat_messages.return_value.messages=[message("foreign","other"),message("command","buyer")]
    client.send_chat_message.return_value={"id":"reply"}
    monkeypatch.setattr("backend.app.integrations.clients.Playerok",lambda _:client)
    poll(NS(db=db));poll(NS(db=db))
    client.send_chat_message.assert_called_once()
    assert len(db.list("notification"))==1
    assert db.list("notification")[0]["event"]=="support"
    assert db.get("support_request","chat:command")["state"]=="SENT"
    assert not db.get("support_request","chat:foreign")


def test_unknown_reply_does_not_repeat_and_requests_are_rate_limited(db,monkeypatch):
    db.put("support_cursor","main",{"started_at":time.time()-10,"offset":0})
    db.put("order","deal",{"chat_id":"chat","buyer_id":"buyer"})
    messages=[NS(id="1",user=NS(id="buyer"),text="!продавец",created_at=datetime.now(timezone.utc))]
    client=Mock();client.account.return_value.get_chat_messages.return_value.messages=messages
    client.send_chat_message.side_effect=TimeoutError()
    monkeypatch.setattr("backend.app.integrations.clients.Playerok",lambda _:client)
    poll(NS(db=db));messages.append(NS(id="2",user=NS(id="buyer"),text="!продавец",created_at=datetime.now(timezone.utc)))
    poll(NS(db=db))
    client.send_chat_message.assert_called_once()
    assert len(db.list("notification"))==1
    assert db.get("support_request","chat:1")["state"]=="UNKNOWN"
