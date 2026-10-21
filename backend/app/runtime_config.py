"""Dashboard-owned connection credentials. Values never leave encrypted storage."""
import time
from .db import Secret

CONFIG_KEYS={"TELEGRAM_BOT_TOKEN","OWNER_TELEGRAM_USERNAME","TELEGRAM_PROXY","MRKT_TELEGRAM_API_ID",
             "MRKT_TELEGRAM_API_HASH","MRKT_MTPROTO_PROXY","MRKT_API_URL","PLAYEROK_USER_AGENT",
             "PLAYEROK_PROXY","PLAYEROK_REQUEST_TIMEOUT","TELEGRAM_USER_SESSION","TELEGRAM_SESSION_DISABLED","PUBLIC_ORIGIN"}

def save_config(db,values):
    if set(values)-CONFIG_KEYS:raise ValueError("Неизвестная настройка подключения")
    with db.tx() as s:
        for name,value in values.items():
            key="config:"+name
            encrypted=db.cipher.encrypt(str(value).encode()).decode()
            row=s.get(Secret,key)
            if row:row.value=encrypted
            else:s.add(Secret(name=key,value=encrypted))
    db.put("runtime_config","revision",{"changed_at":time.time()})

def has_user_session(db):
    c=db.config
    return c.get("TELEGRAM_SESSION_DISABLED")!="1" and bool(c.get("TELEGRAM_USER_SESSION") or c.session_path.exists())

def public_config(db):
    c=db.config
    return {"bot_configured":bool(c.get("TELEGRAM_BOT_TOKEN")),"owner_username":c.get("OWNER_TELEGRAM_USERNAME"),
            "owner_chat_id":db.settings().get("owner_chat_id"),"api_id":c.get("MRKT_TELEGRAM_API_ID"),
            "api_hash_configured":bool(c.get("MRKT_TELEGRAM_API_HASH")),"user_session_configured":has_user_session(db),
            "telegram_account":db.get("telegram_account","main"),"playerok_cookies_configured":bool(db.secret("playerok_cookies")),
            "playerok_user_agent":c.playerok_user_agent,"playerok_request_timeout":c.playerok_request_timeout,
            "mrkt_api_url":c.get("MRKT_API_URL","https://api.tgmrkt.io/api/v1"),
            "public_origin":c.get("PUBLIC_ORIGIN"),
            "playerok_proxy_configured":bool(c.get("PLAYEROK_PROXY")),"telegram_proxy_configured":bool(c.get("TELEGRAM_PROXY")),
            "mtproto_proxy_configured":bool(c.get("MRKT_MTPROTO_PROXY"))}

def import_environment(db):
    values={key:db.config.env[key] for key in CONFIG_KEYS if key in db.config.env and db.secret("config:"+key) is None}
    if values:save_config(db,values)
    return len(values)
