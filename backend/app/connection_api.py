import re
from urllib.parse import urlsplit,parse_qs
from fastapi import APIRouter,HTTPException
from pydantic import Field,field_validator
from .schemas import Strict
from .runtime_config import public_config,save_config
from . import telegram_login

class ConnectionSettings(Strict):
    bot_token:str|None=Field(default=None,max_length=200)
    owner_username:str|None=Field(default=None,max_length=32)
    owner_chat_id:int|None=Field(default=None,ge=1)
    api_id:int|None=Field(default=None,ge=1,le=2147483647)
    api_hash:str|None=Field(default=None,max_length=64)
    playerok_user_agent:str|None=Field(default=None,min_length=10,max_length=1000)
    playerok_request_timeout:int|None=Field(default=None,ge=5,le=120)
    playerok_proxy:str|None=Field(default=None,max_length=1000)
    telegram_proxy:str|None=Field(default=None,max_length=1000)
    mtproto_proxy:str|None=Field(default=None,max_length=1000)
    mrkt_api_url:str|None=Field(default=None,max_length=300)
    public_origin:str|None=Field(default=None,max_length=300)

    @field_validator("public_origin")
    @classmethod
    def origin(cls,v):
        if v:
            v=v.rstrip("/");p=urlsplit(v)
            if p.scheme!="https" or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:raise ValueError("Публичный адрес: только https://домен без пути и параметров")
        return v

    @field_validator("bot_token")
    @classmethod
    def token(cls,v):
        if v and not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{20,}",v.strip()):raise ValueError("Неверный формат токена BotFather")
        return v.strip() if v else v
    @field_validator("api_hash")
    @classmethod
    def hash(cls,v):
        if v and not re.fullmatch(r"[A-Fa-f0-9]{32}",v.strip()):raise ValueError("API Hash должен содержать 32 символа")
        return v.strip() if v else v
    @field_validator("owner_username")
    @classmethod
    def owner(cls,v):
        if v:
            v=v.strip().lstrip("@")
            if not re.fullmatch(r"[A-Za-z0-9_]{5,32}",v):raise ValueError("Неверное имя владельца")
        return v
    @field_validator("playerok_proxy","telegram_proxy")
    @classmethod
    def proxy(cls,v):
        if v:
            p=urlsplit(v)
            if p.scheme not in {"http","https","socks5","socks5h"} or not p.hostname or not p.port:raise ValueError("Прокси: protocol://host:port")
        return v
    @field_validator("mtproto_proxy")
    @classmethod
    def mtproxy(cls,v):
        if v:
            p=urlsplit(v)
            if p.scheme=="tg":
                q=parse_qs(p.query)
                if not all(q.get(k) for k in ["server","port","secret"]):raise ValueError("Неполная ссылка MTProxy")
                if not 1<=int(q["port"][0])<=65535:raise ValueError("Неверный порт MTProxy")
            elif not p.hostname or not p.port or not (p.username or p.password):raise ValueError("MTProxy: tg://proxy?server=...&port=...&secret=...")
        return v
    @field_validator("mrkt_api_url")
    @classmethod
    def url(cls,v):
        if v:
            p=urlsplit(v)
            if p.scheme!="https" or not p.hostname or p.username or p.password or p.query or p.fragment:raise ValueError("Адрес MRKT должен быть HTTPS URL без логина и параметров")
        return v.rstrip("/") if v else v

class LoginStart(Strict):
    phone:str=Field(pattern=r"^\+[1-9]\d{7,14}$")

class LoginInput(Strict):
    flow_id:str=Field(min_length=20,max_length=64)
    value:str=Field(min_length=1,max_length=256)

class LoginFlow(Strict):flow_id:str=Field(min_length=20,max_length=64)

def ensure_connection_change_safe(db):
    settings=db.settings()
    if not settings["emergency_stop"] and any(settings[k] for k in ("repricing_enabled","purchases_enabled","transfers_enabled","promotions_enabled")):
        raise ValueError("Для смены подключений приостановите автоматизацию кнопкой «Стоп»")

def router(db):
    routes=APIRouter(prefix="/api/v1/connections")
    @routes.get("/config")
    def read_config()->dict:return public_config(db)

    @routes.patch("/config")
    def update_config(payload:ConnectionSettings)->dict:
        ensure_connection_change_safe(db)
        data=payload.model_dump(exclude_unset=True,exclude_none=True)
        if set(data)&{"api_id","api_hash","mtproto_proxy"} and telegram_login.status(db)["state"] not in telegram_login.TERMINAL|{"IDLE"}:
            raise ValueError("Сначала завершите или отмените текущий вход в Telegram")
        mapping={"bot_token":"TELEGRAM_BOT_TOKEN","owner_username":"OWNER_TELEGRAM_USERNAME","api_id":"MRKT_TELEGRAM_API_ID",
                 "api_hash":"MRKT_TELEGRAM_API_HASH","playerok_user_agent":"PLAYEROK_USER_AGENT","playerok_request_timeout":"PLAYEROK_REQUEST_TIMEOUT",
                 "playerok_proxy":"PLAYEROK_PROXY","telegram_proxy":"TELEGRAM_PROXY","mtproto_proxy":"MRKT_MTPROTO_PROXY","mrkt_api_url":"MRKT_API_URL","public_origin":"PUBLIC_ORIGIN"}
        token_changed="bot_token" in data and data["bot_token"]!=db.config.get("TELEGRAM_BOT_TOKEN")
        owner_changed="owner_username" in data and data["owner_username"]!=db.config.get("OWNER_TELEGRAM_USERNAME")
        if token_changed or owner_changed:db.configure(notifications_enabled=False,owner_chat_id=None,bot_offset=None)
        if "owner_chat_id" in data:db.configure(owner_chat_id=data["owner_chat_id"])
        save_config(db,{mapping[k]:v for k,v in data.items() if k in mapping})
        if token_changed:db.put("connection","telegram",{"status":"NOT_CHECKED","last_error":None,"username":None},merge=False)
        db.log("connection_config","Параметры подключений сохранены",", ".join(data))
        return public_config(db)

    @routes.get("/telegram-user/login")
    def login_status()->dict:
        telegram_login.expire(db)
        return telegram_login.status(db)

    @routes.post("/telegram-user/start")
    def login_start(payload:LoginStart)->dict:
        ensure_connection_change_safe(db)
        if not db.config.get("MRKT_TELEGRAM_API_ID") or not db.config.get("MRKT_TELEGRAM_API_HASH"):raise ValueError("Сначала сохраните Telegram API ID и API Hash")
        return telegram_login.begin(db,payload.phone)

    @routes.post("/telegram-user/code")
    def login_code(payload:LoginInput)->dict:
        ensure_connection_change_safe(db)
        code=re.sub(r"\s","",payload.value)
        if not re.fullmatch(r"\d{4,8}",code):raise ValueError("Введите цифровой код из Telegram")
        return telegram_login.provide(db,payload.flow_id,"code",code)

    @routes.post("/telegram-user/password")
    def login_password(payload:LoginInput)->dict:
        ensure_connection_change_safe(db)
        return telegram_login.provide(db,payload.flow_id,"password",payload.value)

    @routes.post("/telegram-user/cancel")
    def login_cancel(payload:LoginFlow)->dict:return telegram_login.cancel(db,payload.flow_id)

    @routes.delete("/telegram-user")
    def user_disconnect()->dict:
        ensure_connection_change_safe(db)
        if not telegram_login.status(db)["state"] in {"IDLE","AUTHORIZED","ERROR","CANCELLED","EXPIRED"}:raise ValueError("Сначала отмените текущий вход")
        db.configure(purchases_enabled=False,transfers_enabled=False,delivery_tested=False)
        save_config(db,{"TELEGRAM_USER_SESSION":"","TELEGRAM_SESSION_DISABLED":"1"})
        db.put("telegram_account","main",{},merge=False)
        db.put("connection","mrkt",{"status":"NOT_CONFIGURED","last_error":None},merge=False)
        return {"disconnected":True}

    @routes.delete("/playerok")
    def playerok_disconnect()->dict:
        ensure_connection_change_safe(db)
        db.forget_secret("playerok_cookies")
        db.configure(repricing_enabled=False,promotions_enabled=False,purchases_enabled=False,transfers_enabled=False,
                     playerok_account_id=None,playerok_username=None,orders_baselined=False)
        db.put("connection","playerok",{"status":"NOT_CONFIGURED","last_error":None},merge=False)
        return {"disconnected":True}
    return routes
