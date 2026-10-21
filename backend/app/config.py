from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def dotenv(path: Path) -> dict[str, str]:
    if not path.exists(): return {}
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result

class Config:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.data = root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.env = dotenv(root / ".env")
        self.runtime_reader = None
        self.db_path = self.data / "manager.sqlite3"
        self.key_path = self.data / "secrets.key"
        self.session_path = self.data / "mrkt_user.session"

    @property
    def playerok_user_agent(self):return self.get("PLAYEROK_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    @property
    def playerok_proxy(self):return self.get("PLAYEROK_PROXY") or None
    @property
    def playerok_request_timeout(self):return int(self.get("PLAYEROK_REQUEST_TIMEOUT", "20"))

    def get(self, key: str, default: str = "") -> str:
        if self.runtime_reader:
            value=self.runtime_reader(key)
            if value is not None:return value or default
        return os.environ.get(key) or self.env.get(key) or default
