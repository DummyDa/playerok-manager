import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet
from backend.app.config import Config
from backend.app.db import Database,Base
from scripts.import_legacy import migrate

def test_import_is_readonly_idempotent_and_safe(tmp_path):
    source=tmp_path/"old";target=tmp_path/"new";(source/"data").mkdir(parents=True)
    key=Fernet.generate_key();(source/"data/playerok_manager.key").write_bytes(key)
    (source/".env").write_text("TELEGRAM_BOT_TOKEN=private-token\nAUTO_DELIVERY_ENABLED=true\nTELEGRAM_GIFT_TRANSFER_BACKEND=web\n")
    path=source/"data/playerok_manager.sqlite3"
    with sqlite3.connect(path) as c:
        c.executescript("""CREATE TABLE app_settings(id INTEGER, encrypted_playerok_cookies TEXT, playerok_account_id TEXT, playerok_account_username TEXT,owner_chat_id INTEGER,listing_description_template TEXT);
        CREATE TABLE processed_deals(deal_id TEXT,processed_at TEXT);
        CREATE TABLE auto_delivery_jobs(deal_id TEXT,stage TEXT,nft_name TEXT,model_name TEXT,telegram_username TEXT);
        CREATE TABLE event_log(id INTEGER,event_type TEXT,title TEXT,details TEXT,level TEXT,created_at TEXT);""")
        c.execute("INSERT INTO app_settings VALUES(1,?,?,?,?,?)",(Fernet(key).encrypt(b"cookie=secret").decode(),"account","owner",123,"template"))
        c.execute("INSERT INTO processed_deals VALUES('done','2026-01-01')")
        c.execute("INSERT INTO auto_delivery_jobs VALUES('manual','WAITING_MANUAL','Gift','Model','recipient')")
        c.execute("INSERT INTO event_log VALUES(1,'event','title','private-token','INFO','2026-01-01')")
    before=path.read_bytes()
    report=migrate(source,target,False)
    assert not target.exists()
    assert "private-token" not in str(report)
    db=Database(Config(target));Base.metadata.create_all(db.engine)
    migrate(source,target,True);migrate(source,target,True)
    assert path.read_bytes()==before
    assert db.secret("playerok_cookies")=="cookie=secret"
    assert len(db.list("processed"))==1
    assert db.get("order","manual")["stage"]=="MANUAL"
    assert "dry_run" not in db.settings()
    assert db.settings()["purchases_enabled"] is False
    assert not (target/".env").exists()
    assert db.config.get("TELEGRAM_BOT_TOKEN")=="private-token"
    assert "private-token" not in str(db.list("event"))
