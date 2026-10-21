"""Move legacy .env connections into encrypted dashboard storage, preserving a private backup."""
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.config import Config
from backend.app.db import Database
from backend.app.runtime_config import CONFIG_KEYS, import_environment

def main():
    db=Database(Config());db.initialize()
    source=ROOT/".env"
    if not source.exists():print("No legacy .env to migrate.");return
    raw=source.read_text(encoding="utf-8-sig")
    import_environment(db)
    # Existing encrypted values take precedence over stale environment values.
    names=set(db.config.env)&CONFIG_KEYS
    if not all(db.secret("config:"+key) is not None for key in names):raise SystemExit("Migration verification failed; .env retained.")
    if not names:print("No connection settings remain in .env.");return
    backup=ROOT/"data/private";backup.mkdir(parents=True,exist_ok=True)
    (backup/f"env-before-dashboard-{time.time_ns()}.bak").write_text(raw,encoding="utf-8")
    remaining=[line for line in raw.splitlines() if line.split("=",1)[0].strip() not in names]
    source.write_text("\n".join(remaining)+"\n",encoding="utf-8")
    print(f"Migrated {len(names)} connection settings. Private backup retained under data/private.")

if __name__=="__main__":main()
