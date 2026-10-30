from __future__ import annotations
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event, String, Text, Integer, Float, JSON, select, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import Config

def uid(): return uuid.uuid4().hex
def now(): return time.time()

class Base(DeclarativeBase): pass

class Entity(Base):
    __tablename__ = "entities"
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(Float, default=now)
    updated: Mapped[float] = mapped_column(Float, default=now, index=True)

class Secret(Base):
    __tablename__ = "secrets"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)

class Login(Base):
    __tablename__ = "sessions"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    csrf: Mapped[str] = mapped_column(String)
    expires: Mapped[float] = mapped_column(Float)

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    dedupe: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String, default="READY", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due: Mapped[float] = mapped_column(Float, default=now, index=True)
    lease: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

DEFAULTS = {
    "delivery_transport": "mtproto",
    "emergency_stop": False, "repricing_enabled": False,
    "purchases_enabled": False, "transfers_enabled": False, "promotions_enabled": False,
    "notifications_enabled": False, "delivery_tested": False,
    "stars_rub": None, "transfer_stars_reserve": 25, "purchase_limit_rub": "0",
    "ton_rate_mode": "auto", "manual_ton_rub": None,
    "daily_purchase_limit_rub": "0", "daily_promotion_limit_rub": "0",
    "quote_interval": 3600, "competitor_interval": 900, "quote_max_age": 7200,
    "description_template": "", "owner_chat_id": None, "playerok_account_id": None,
    "buyer_paid_enabled": False, "buyer_delivered_enabled": False,
    "buyer_paid_template": "Спасибо за оплату! Заказ {order_id} получен. Готовим заказ к исполнению.",
    "buyer_delivered_template": "Подарок {gift} передан {recipient}! Ссылка: {gift_link}\nСпасибо за покупку!",
    "playerok_username": None, "bot_offset": None, "orders_baselined": False,
}

class Database:
    def __init__(self, config: Config):
        self.config = config
        self.engine = create_engine(f"sqlite:///{config.db_path.as_posix()}", connect_args={"timeout": 15})
        @event.listens_for(self.engine, "connect")
        def setup(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=15000")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        if not config.key_path.exists(): config.key_path.write_bytes(Fernet.generate_key())
        self.cipher = Fernet(config.key_path.read_bytes().strip())
        from .runtime_config import CONFIG_KEYS
        def read_runtime(key):
            if key not in CONFIG_KEYS:return None
            from sqlalchemy.exc import OperationalError
            try:return self.secret("config:"+key)
            except OperationalError as exc:
                if "no such table: secrets" in str(exc):return None
                raise
        config.runtime_reader=read_runtime

    @contextmanager
    def tx(self):
        with self.sessions.begin() as s: yield s

    def get(self, kind, id="default"):
        with self.sessions() as s:
            row = s.get(Entity, (kind, str(id)))
            return {**row.data, "id": row.id, "updated_at": row.updated} if row else None

    def list(self, kind, limit=1000):
        with self.sessions() as s:
            return [{**r.data, "id": r.id, "updated_at": r.updated} for r in s.scalars(select(Entity).where(Entity.kind == kind).order_by(Entity.updated.desc()).limit(limit))]

    def put(self, kind, id, data, merge=True):
        with self.tx() as s:
            row = s.get(Entity, (kind, str(id)))
            clean = {k: v for k, v in data.items() if k not in {"id", "updated_at"}}
            if row:
                row.data = {**row.data, **clean} if merge else clean
                row.updated = now()
            else: s.add(Entity(kind=kind, id=str(id), data=clean))
        return self.get(kind, id)

    def settings(self):
        data={**DEFAULTS, **(self.get("settings") or {})};data.pop("dry_run",None)
        return data

    def patch_existing(self,kind,id,data):
        with self.tx() as s:
            row=s.get(Entity,(kind,str(id)))
            if row is None:return False
            row.data={**row.data,**data};row.updated=now()
            return True
    def configure(self, **data): return self.put("settings", "default", data)

    def secret(self, name, value=None):
        with self.tx() as s:
            row = s.get(Secret, name)
            if value is not None:
                encrypted = self.cipher.encrypt(value.encode()).decode()
                if row: row.value = encrypted
                else: s.add(Secret(name=name, value=encrypted))
                return None
            return self.cipher.decrypt(row.value.encode()).decode() if row else None

    def forget_secret(self,name):
        with self.tx() as s:
            row=s.get(Secret,name)
            if row:s.delete(row)

    def redact(self, message):
        text = str(message)
        values = [v for k, v in self.config.env.items() if any(x in k for x in ("TOKEN", "HASH", "SECRET", "PROXY")) and len(v) > 3]
        with self.sessions() as s:
            for r in s.scalars(select(Secret)):
                try:
                    value=self.cipher.decrypt(r.value.encode()).decode()
                    values.append(value)
                    if r.name=="playerok_cookies":
                        values.extend(part.strip() for part in value.split(";") if len(part.strip())>8)
                        values.extend(part.split("=",1)[1].strip() for part in value.split(";") if "=" in part and len(part.split("=",1)[1].strip())>12)
                except Exception: pass
        for value in sorted({v for v in values if isinstance(v,str) and len(v)>3}, key=len, reverse=True): text = text.replace(value, "[скрыто]")
        return text[:1500]

    def log(self, action, title, detail="", level="INFO", **refs):
        return self.put("event", uid(), {"action": action, "title": title, "detail": self.redact(detail), "level": level, **refs})

    def enqueue(self, kind, payload=None, key=None, due=None):
        from sqlalchemy.exc import IntegrityError
        key = key or kind
        try:
            with self.tx() as s:
                existing = s.scalar(select(Task).where(Task.dedupe == key))
                if existing: return existing.id
                task = Task(id=uid(), kind=kind, payload=payload or {}, dedupe=key, due=due or now())
                s.add(task)
                return task.id
        except IntegrityError:
            with self.sessions() as s: return s.scalar(select(Task.id).where(Task.dedupe == key))

    def initialize(self):
        previous=self.get("settings") or {}
        legacy_preview=previous.pop("dry_run",False)
        if legacy_preview:
            previous.update(emergency_stop=True,repricing_enabled=False,purchases_enabled=False,transfers_enabled=False,promotions_enabled=False)
        self.put("settings","default",{**DEFAULTS,**previous},merge=False)
        if not self.get("fees"):
            self.put("fees", "default", {"name": "Основные комиссии", "configured": False, "fees": []})
        from .runtime_config import import_environment
        import_environment(self)
        if not self.get("schema_feature","premium_pricing"):
            estimates=self.list("estimate",100000)
            for estimate in estimates:
                self.put("estimate",estimate["id"],{"status":"STALE","error":"Нужен новый расчёт с учётом премиума"},merge=False)
            self.put("schema_feature","premium_pricing",{"version":1})
            if estimates:self.enqueue("reprice_preview")

    def fingerprint(self):
        return hashlib.sha256(str(self.config.root).lower().encode()).hexdigest()[:12]
