"""Read-only source import. Without --apply prints a secret-free inventory."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from backend.app.config import Config, ROOT, dotenv
from backend.app.db import Database, DEFAULTS

ENV_KEYS = ["TELEGRAM_BOT_TOKEN","OWNER_TELEGRAM_USERNAME","TELEGRAM_PROXY","MRKT_TELEGRAM_API_ID","MRKT_TELEGRAM_API_HASH",
            "MRKT_MTPROTO_PROXY","MRKT_API_URL","PLAYEROK_USER_AGENT","PLAYEROK_PROXY","PLAYEROK_REQUEST_TIMEOUT"]

def migrate(source: Path, target: Path, apply=False):
    env = dotenv(source/".env")
    db_path = Path(env.get("DATABASE_FILE","data/playerok_manager.sqlite3"))
    if not db_path.is_absolute(): db_path = source/db_path
    connection = sqlite3.connect(db_path.as_uri()+"?mode=ro",uri=True)
    connection.row_factory = sqlite3.Row
    report = {"source":str(source),"target":str(target),"env_keys_present":[k for k in ENV_KEYS if env.get(k)],
              "counts":{table:connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                        for table in ["processed_deals","auto_delivery_jobs","event_log"]},"mode":"apply" if apply else "preview"}
    if not apply:
        connection.close(); return report
    target.mkdir(parents=True,exist_ok=True)
    config = Config(target)
    db = Database(config)
    from backend.app.runtime_config import save_config
    save_config(db,{k:env[k] for k in ENV_KEYS if env.get(k) and db.secret("config:"+k) is None})
    # The schema is migrated by bootstrap before import; tests may create it explicitly.
    old = dict(connection.execute("SELECT * FROM app_settings WHERE id=1").fetchone())
    key = Path(env.get("ENCRYPTION_KEY_FILE","data/playerok_manager.key"))
    if not key.is_absolute(): key = source/key
    if old.get("encrypted_playerok_cookies") and not db.secret("playerok_cookies"):
        cookies = Fernet(key.read_bytes().strip()).decrypt(old["encrypted_playerok_cookies"].encode()).decode()
        db.secret("playerok_cookies", cookies)
    if not db.get("import","legacy"):
        db.configure(**DEFAULTS)
        db.configure(playerok_account_id=old.get("playerok_account_id"),playerok_username=old.get("playerok_account_username"),
            owner_chat_id=old.get("owner_chat_id"),description_template=old.get("listing_description_template") or "")
    for row in connection.execute("SELECT * FROM processed_deals"):
        db.put("processed",str(row["deal_id"]),{"source":"legacy","processed_at":str(row["processed_at"])})
    for row in connection.execute("SELECT * FROM auto_delivery_jobs"):
        id = str(row["deal_id"])
        if not db.get("order",id):
            db.put("order",id,{"stage":"MANUAL","source":"legacy","legacy":dict(row),"title":str(row["nft_name"] or "")+" — "+str(row["model_name"] or ""),
                "recipient":row["telegram_username"],"reason":"Импортировано из прототипа; автоматическое повторение запрещено"})
    for row in connection.execute("SELECT * FROM event_log"):
        id = "legacy:"+str(row["id"])
        if not db.get("event",id): db.put("event",id,{"action":row["event_type"],"title":row["title"],"detail":db.redact(row["details"] or ""),"level":row["level"],"legacy_date":str(row["created_at"])})
    src_session = Path(env.get("MRKT_TELEGRAM_SESSION_FILE","data/mrkt_user.session"))
    if not src_session.is_absolute(): src_session = source/src_session
    if src_session.exists() and not config.session_path.exists():
        with sqlite3.connect(src_session.as_uri()+"?mode=ro",uri=True) as original, sqlite3.connect(config.session_path) as new:
            original.backup(new)
    db.put("import","legacy",{"source":str(source),"report":report})
    db.initialize()
    connection.close(); db.engine.dispose()
    return report

if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--apply",action="store_true")
    args=p.parse_args();print(json.dumps(migrate(args.source,ROOT,args.apply),ensure_ascii=False,indent=2))
