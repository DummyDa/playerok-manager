"""One-step Telegram authorization, executed exclusively by the background worker.

Phone, code, 2FA password, auth key and phone-code hash are encrypted and never
stored in queue payloads or returned by the API. Pending credentials expire.
"""
import asyncio
import json
import time
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from .db import uid
from .runtime_config import save_config

TERMINAL={"AUTHORIZED","ERROR","EXPIRED","CANCELLED"}

def secret_name(flow):return "telegram-login:"+flow

def status(db):
    state=db.get("telegram_login","current")
    if not state:return {"state":"IDLE"}
    return {k:state[k] for k in ("flow_id","state","expires","error","username") if k in state}

def expire(db):
    state=status(db)
    if state["state"] not in TERMINAL|{"IDLE"} and state.get("expires",0)<=time.time():
        db.forget_secret(secret_name(state["flow_id"]))
        db.put("telegram_login","current",{"state":"EXPIRED","error":"Время входа истекло. Начните вход заново."})

def begin(db,phone):
    expire(db)
    old=status(db)
    if old["state"] not in TERMINAL|{"IDLE"}:raise ValueError("Вход уже начат. Завершите или отмените его.")
    db.configure(delivery_tested=False)
    flow=uid()
    db.secret(secret_name(flow),json.dumps({"phone":phone}))
    db.put("telegram_login","current",{"flow_id":flow,"state":"QUEUED","expires":time.time()+600,"error":None},merge=False)
    db.enqueue("telegram_auth",{"flow_id":flow,"action":"start"},key="telegram-auth:"+flow+":start")
    return status(db)

def provide(db,flow,action,value):
    expire(db);state=status(db)
    required="CODE_REQUIRED" if action=="code" else "PASSWORD_REQUIRED"
    if state.get("flow_id")!=flow or state["state"]!=required:raise ValueError("Этот шаг входа больше не активен. Обновите состояние.")
    raw=db.secret(secret_name(flow))
    if not raw:raise ValueError("Срок входа истёк. Начните заново.")
    data=json.loads(raw);data["input"]=value
    db.secret(secret_name(flow),json.dumps(data))
    db.put("telegram_login","current",{"state":"VERIFYING","error":None})
    db.enqueue("telegram_auth",{"flow_id":flow,"action":action},key="telegram-auth:"+uid())
    return status(db)

def cancel(db,flow):
    state=status(db)
    if state.get("flow_id")==flow:
        db.forget_secret(secret_name(flow))
        db.put("telegram_login","current",{"state":"CANCELLED","error":None})
    return status(db)

def step(db,flow,action,client_factory=None):
    expire(db);state=status(db)
    if state.get("flow_id")!=flow or state["state"] in TERMINAL|{"IDLE"}:return {"ignored":True}
    raw=db.secret(secret_name(flow))
    if not raw:return {"ignored":True}
    data=json.loads(raw)
    entered=data.pop("input",None)
    # Remove one-time input before doing any network work, including failures.
    db.secret(secret_name(flow),json.dumps(data))
    expected="QUEUED" if action=="start" else "VERIFYING"
    if state["state"]!=expected:return {"ignored":True}
    if action!="start" and entered is None:
        db.put("telegram_login","current",{"state":"ERROR","error":"Проверка входа была прервана. Начните заново."})
        db.forget_secret(secret_name(flow));return status(db)
    def active():
        latest=status(db)
        return latest.get("flow_id")==flow and latest["state"] not in TERMINAL and latest.get("expires",0)>time.time()
    def change(**values):
        if active():db.put("telegram_login","current",values)
    async def run():
        from .integrations.clients import telegram_options
        factory=client_factory or TelegramClient
        client=factory(StringSession(data.get("session") or ""),int(db.config.get("MRKT_TELEGRAM_API_ID")),
                       db.config.get("MRKT_TELEGRAM_API_HASH"),timeout=10,connection_retries=1,request_retries=0,
                       flood_sleep_threshold=0,**telegram_options(db.config))
        try:
            if action=="start":change(state="REQUESTING_CODE",error=None)
            await client.connect()
            if action=="start":
                sent=await client.send_code_request(data["phone"])
                if not active():return
                data.update(session=client.session.save(),phone_code_hash=sent.phone_code_hash)
                db.secret(secret_name(flow),json.dumps(data))
                change(state="CODE_REQUIRED",error=None)
                return
            try:
                if action=="code":await client.sign_in(phone=data["phone"],code=entered,phone_code_hash=data["phone_code_hash"])
                elif action=="password":await client.sign_in(password=entered)
                else:raise ValueError("Неизвестный шаг входа")
            except errors.SessionPasswordNeededError:
                if active():
                    data["session"]=client.session.save();db.secret(secret_name(flow),json.dumps(data))
                    change(state="PASSWORD_REQUIRED",error=None)
                return
            me=await client.get_me()
            if not me or getattr(me,"bot",False):raise ValueError("Требуется личный Telegram-аккаунт")
            if not active():return
            save_config(db,{"TELEGRAM_USER_SESSION":client.session.save(),"TELEGRAM_SESSION_DISABLED":"0"})
            db.put("telegram_account","main",{"user_id":me.id,"username":getattr(me,"username",None),"connected_at":time.time()},merge=False)
            change(state="AUTHORIZED",username=getattr(me,"username",None),error=None)
            db.forget_secret(secret_name(flow))
            db.put("connection","mrkt",{"status":"NOT_CHECKED","last_error":None})
            db.enqueue("check",{"name":"mrkt"},key="check:mrkt")
        except errors.PhoneCodeInvalidError:change(state="CODE_REQUIRED",error="Неверный код. Введите код из Telegram ещё раз.")
        except errors.PasswordHashInvalidError:change(state="PASSWORD_REQUIRED",error="Неверный пароль двухэтапной проверки.")
        except errors.FloodWaitError as exc:
            change(state="ERROR",error=f"Telegram ограничил попытки. Повторите через {exc.seconds} секунд.")
            db.forget_secret(secret_name(flow))
        except errors.PhoneCodeExpiredError:
            change(state="EXPIRED",error="Код истёк. Начните вход заново.");db.forget_secret(secret_name(flow))
        except Exception:
            # Telegram exceptions may embed phone, code or password. Never echo them.
            change(state="ERROR",error="Не удалось войти в Telegram. Проверьте API ID, API Hash, номер и соединение.")
            db.forget_secret(secret_name(flow))
        finally:await client.disconnect()
    try:asyncio.run(asyncio.wait_for(run(),timeout=45))
    except Exception:
        change(state="ERROR",error="Telegram не ответил. Начните вход заново.")
        db.forget_secret(secret_name(flow))
    return status(db)
