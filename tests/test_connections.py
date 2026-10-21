import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from telethon import errors
from telethon.crypto import AuthKey
from telethon.sessions import StringSession
from backend.app.main import create_app
from backend.app.db import Task, Secret
from backend.app.runtime_config import save_config, public_config, has_user_session
from backend.app import telegram_login

TOKEN="123456789:"+"testtoken_"*4
HASH="abcd"*8
PHONE="+79990000001"

@pytest.fixture
def client(db):
    with TestClient(create_app(db)) as c:
        r=c.post("/api/v1/auth/setup",json={"password":"local-admin-password"})
        c.headers["X-CSRF-Token"]=r.json()["csrf"]
        yield c

def keys(db):save_config(db,{"MRKT_TELEGRAM_API_ID":"12345","MRKT_TELEGRAM_API_HASH":HASH})

def valid_session():
    s=StringSession();s.set_dc(2,"149.154.167.51",443);s.auth_key=AuthKey(b"a"*256)
    return s.save()

def fake_client(sign_in=None):
    c=SimpleNamespace(session=SimpleNamespace(save=lambda:valid_session()),connect=AsyncMock(),disconnect=AsyncMock(),
                      send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash="private-phone-code-hash")),
                      sign_in=AsyncMock(side_effect=sign_in),get_me=AsyncMock(return_value=SimpleNamespace(id=999,username="example_account",bot=False)))
    return c

def test_dashboard_credentials_are_encrypted_not_returned_or_queued(db,client):
    r=client.patch("/api/v1/connections/config",json={"bot_token":TOKEN,"owner_username":"example_owner","api_id":12345,"api_hash":HASH})
    assert r.status_code==200
    assert r.json()["bot_configured"] and r.json()["api_hash_configured"]
    assert TOKEN not in r.text and HASH not in r.text
    assert db.config.get("TELEGRAM_BOT_TOKEN")==TOKEN
    with db.sessions() as s:
        raw=" ".join(x.value for x in s.scalars(select(Secret)))
    assert TOKEN not in raw and HASH not in raw
    assert TOKEN not in str(db.list("event"))
    for path in ["/settings","/connections","/connections/config","/automation"]:
        text=client.get("/api/v1"+path).text
        assert TOKEN not in text and HASH not in text
    client.patch("/api/v1/connections/config",json={"owner_username":"second_owner"})
    assert db.config.get("TELEGRAM_BOT_TOKEN")==TOKEN

def test_config_applies_without_restart_and_empty_overrides_environment(db,client):
    db.config.env["PLAYEROK_PROXY"]="http://old.example:8888"
    client.patch("/api/v1/connections/config",json={"playerok_proxy":"http://new.example:1234","playerok_request_timeout":45})
    assert db.config.playerok_proxy=="http://new.example:1234"
    assert db.config.playerok_request_timeout==45
    client.patch("/api/v1/connections/config",json={"playerok_proxy":""})
    assert db.config.playerok_proxy is None
    assert db.redact("example")=="example"

def test_secret_validation_errors_do_not_echo_input(db,client):
    invalid="not-a-valid-secret-token"
    r=client.patch("/api/v1/connections/config",json={"bot_token":invalid})
    assert r.status_code==422 and invalid not in r.text
    r=client.post("/api/v1/auth/password",json={"current_password":"secret-password","new_password":"short"})
    assert r.status_code==422 and "secret-password" not in r.text

def test_token_replacement_resets_owner_and_polling(db,client):
    keys(db);db.configure(owner_chat_id=77,bot_offset=123,notifications_enabled=True)
    client.patch("/api/v1/connections/config",json={"bot_token":TOKEN,"owner_username":"example_owner"})
    assert db.settings()["owner_chat_id"] is None
    assert db.settings()["bot_offset"] is None
    assert not db.settings()["notifications_enabled"]

def test_connection_change_requires_pausing_active_trading_and_csrf(db,client):
    client.headers.pop("X-CSRF-Token")
    assert client.patch("/api/v1/connections/config",json={"bot_token":TOKEN}).status_code==403
    db.configure(purchases_enabled=True)
    r=client.post("/api/v1/auth/login",json={"password":"local-admin-password"})
    client.headers["X-CSRF-Token"]=r.json()["csrf"]
    assert client.patch("/api/v1/connections/config",json={"bot_token":TOKEN}).status_code==400

def test_login_start_code_and_success_never_expose_auth_key(db,client):
    keys(db)
    r=client.post("/api/v1/connections/telegram-user/start",json={"phone":PHONE})
    assert r.status_code==200 and PHONE not in r.text
    flow=r.json()["flow_id"]
    c=fake_client();telegram_login.step(db,flow,"start",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="CODE_REQUIRED"
    assert client.post("/api/v1/connections/telegram-user/code",json={"flow_id":flow,"value":"12345"}).status_code==200
    with db.sessions() as s:
        assert "12345" not in str([t.payload for t in s.scalars(select(Task))])
    telegram_login.step(db,flow,"code",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="AUTHORIZED"
    assert has_user_session(db)
    assert db.secret(telegram_login.secret_name(flow)) is None
    assert valid_session() not in client.get("/api/v1/connections/config").text
    c.sign_in.assert_awaited_once_with(phone=PHONE,code="12345",phone_code_hash="private-phone-code-hash")

def test_login_2fa_wrong_password_and_success(db,client):
    keys(db);flow=telegram_login.begin(db,PHONE)["flow_id"]
    c=fake_client();telegram_login.step(db,flow,"start",lambda *a,**kw:c)
    telegram_login.provide(db,flow,"code","12345")
    c.sign_in.side_effect=errors.SessionPasswordNeededError(None)
    telegram_login.step(db,flow,"code",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="PASSWORD_REQUIRED"
    telegram_login.provide(db,flow,"password","private-two-factor-password")
    c.sign_in.side_effect=errors.PasswordHashInvalidError(None)
    telegram_login.step(db,flow,"password",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="PASSWORD_REQUIRED"
    assert "private-two-factor-password" not in db.secret(telegram_login.secret_name(flow))
    telegram_login.provide(db,flow,"password","correct-password")
    c.sign_in.side_effect=None
    telegram_login.step(db,flow,"password",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="AUTHORIZED"

def test_wrong_code_expiry_and_cancel_do_not_replace_existing_session(db):
    keys(db);save_config(db,{"TELEGRAM_USER_SESSION":valid_session()})
    flow=telegram_login.begin(db,PHONE)["flow_id"]
    c=fake_client();telegram_login.step(db,flow,"start",lambda *a,**kw:c)
    telegram_login.provide(db,flow,"code","12345")
    c.sign_in.side_effect=errors.PhoneCodeInvalidError(None)
    telegram_login.step(db,flow,"code",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="CODE_REQUIRED"
    telegram_login.cancel(db,flow)
    telegram_login.step(db,flow,"code",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="CANCELLED"
    assert db.config.get("TELEGRAM_USER_SESSION")==valid_session()
    flow=telegram_login.begin(db,PHONE)["flow_id"]
    db.put("telegram_login","current",{"expires":time.time()-1})
    telegram_login.expire(db)
    assert telegram_login.status(db)["state"]=="EXPIRED"
    assert db.secret(telegram_login.secret_name(flow)) is None

def test_restart_continues_login_challenge_from_encrypted_state(db):
    from backend.app.db import Database
    from backend.app.config import Config
    keys(db);flow=telegram_login.begin(db,PHONE)["flow_id"]
    c=fake_client();telegram_login.step(db,flow,"start",lambda *a,**kw:c)
    reopened=Database(Config(db.config.root))
    telegram_login.provide(reopened,flow,"code","12345")
    telegram_login.step(reopened,flow,"code",lambda *a,**kw:c)
    assert telegram_login.status(reopened)["state"]=="AUTHORIZED"
    reopened.engine.dispose()

def test_disconnect_disables_new_trades_and_keeps_history(db,client):
    keys(db);save_config(db,{"TELEGRAM_USER_SESSION":valid_session()})
    db.configure(purchases_enabled=True,transfers_enabled=True,delivery_tested=True,emergency_stop=True)
    db.put("ledger","previous",{"amount_rub":"25"})
    assert client.delete("/api/v1/connections/telegram-user").status_code==200
    assert not has_user_session(db)
    assert not db.settings()["purchases_enabled"] and not db.settings()["transfers_enabled"]
    assert db.get("ledger","previous") is not None

def test_password_change_keeps_current_session_and_revokes_other_sessions(db,client):
    with TestClient(create_app(db)) as other:
        other.post("/api/v1/auth/login",json={"password":"local-admin-password"})
        r=client.post("/api/v1/auth/password",json={"current_password":"local-admin-password","new_password":"new-local-password"})
        assert r.status_code==200
        assert client.get("/api/v1/settings").status_code==200
        assert other.get("/api/v1/settings").status_code==401

def test_recovery_does_not_replay_an_interrupted_telegram_step(db):
    from backend.app.operations import recover
    keys(db);flow=telegram_login.begin(db,PHONE)["flow_id"]
    db.put("telegram_login","current",{"state":"REQUESTING_CODE"})
    with db.tx() as s:
        task=s.scalar(select(Task).where(Task.kind=="telegram_auth"));task.state="RUNNING"
    recover(db)
    assert telegram_login.status(db)["state"]=="ERROR"
    assert db.secret(telegram_login.secret_name(flow)) is None
    with db.sessions() as s:
        task=s.scalar(select(Task).where(Task.kind=="telegram_auth"))
        assert task.state=="FAILED" and task.dedupe is None

def test_public_origin_setting_applies_without_restart(db,client):
    assert client.patch("/api/v1/connections/config",json={"public_origin":"https://panel.example.com"}).status_code==200
    response=client.get("/api/v1/settings",headers={"host":"panel.example.com"})
    assert response.status_code==200
    response=client.get("/api/v1/settings",headers={"host":"untrusted.example.com"})
    assert response.status_code==400
    assert client.patch("/api/v1/connections/config",json={"public_origin":""}).status_code==200
    assert client.get("/api/v1/settings",headers={"host":"panel.example.com"}).status_code==400

def test_cancel_while_signing_in_does_not_replace_saved_session(db):
    keys(db)
    flow=telegram_login.begin(db,PHONE)["flow_id"]
    c=fake_client();telegram_login.step(db,flow,"start",lambda *a,**kw:c)
    telegram_login.provide(db,flow,"code","12345")
    async def cancel_before_response(**kwargs):telegram_login.cancel(db,flow)
    c.sign_in.side_effect=cancel_before_response
    telegram_login.step(db,flow,"code",lambda *a,**kw:c)
    assert telegram_login.status(db)["state"]=="CANCELLED"
    assert not db.config.get("TELEGRAM_USER_SESSION")
