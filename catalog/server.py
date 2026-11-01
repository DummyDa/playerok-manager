"""Customer catalog; real checkout intents are executed by the main worker."""
from __future__ import annotations

import hashlib
import secrets
import ipaddress
import os
import json
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal
from pathlib import Path

import requests
import anyio
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from backend.app.catalog_pricing import public_policy, price as catalog_sale_price
from backend.app.db import DEFAULTS, Database
from backend.app.catalog_pricing import context as pricing_context
from backend.app.pricing import calculate, D
from backend.app.config import Config
from backend.app.integrations.clients import Mrkt
from backend.app.integrations.mrkt_availability import gift_block_reason
from backend.app.process_lock import process_lock

DATA = HERE / "data"
DATA.mkdir(exist_ok=True)
(DATA / "images").mkdir(exist_ok=True)
INTERVAL = 120
STALE_AFTER = 180


class MainSettings:
    """Read parent credentials without copying secrets or writing its database."""
    def __init__(self):
        self.config = Config(ROOT)
        if not self.config.db_path.exists() or not self.config.key_path.exists():
            raise RuntimeError("Сначала настройте основной проект")
        self.cipher = Fernet(self.config.key_path.read_bytes().strip())
        self.config.runtime_reader = self.read_secret
        self.throttle = {}

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.config.db_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            yield conn
        finally:
            conn.close()

    def settings(self):
        return {**DEFAULTS,**self.get("settings")}

    def read_secret(self, key):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM secrets WHERE name=?", ("config:" + key,)).fetchone()
        return self.cipher.decrypt(row[0].encode()).decode() if row else None

    def get(self, kind, id="default"):
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM entities WHERE kind=? AND id=?", (kind, id)).fetchone()
        value = json.loads(row[0]) if row else {}
        if kind == "throttle":
            own = self.throttle.get(id, {})
            value = {**value, **own, "next": max(value.get("next", 0), own.get("next", 0))}
        return value

    def put(self, kind, id, value):
        if kind != "throttle":
            raise RuntimeError("Collector can only update request throttling")
        db=Database(Config(ROOT))
        try:db.put(kind,id,value)
        finally:db.engine.dispose()

    def reserve_throttle(self,service,interval):
        db=Database(Config(ROOT))
        try:return db.reserve_throttle(service,interval)
        finally:db.engine.dispose()


@contextmanager
def connect():
    conn = sqlite3.connect(DATA / "catalog.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize_storage():
    with connect() as conn:
        conn.executescript("""
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS gifts (
        id TEXT PRIMARY KEY, collection TEXT, model TEXT, backdrop TEXT, symbol TEXT,
        number INTEGER, price INTEGER, checked REAL, payload TEXT);
      CREATE INDEX IF NOT EXISTS gifts_filter ON gifts(collection,model,backdrop,symbol,price,id);
      CREATE INDEX IF NOT EXISTS gifts_price ON gifts(price,id);
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, path TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS request_budgets (id TEXT PRIMARY KEY, count INTEGER, expires REAL);
      CREATE TABLE IF NOT EXISTS visitors (digest TEXT PRIMARY KEY, expires REAL);
      CREATE TABLE IF NOT EXISTS browse_queries (
        id TEXT PRIMARY KEY, signature TEXT, cursor TEXT, total INTEGER,
        updated REAL, exhausted INTEGER DEFAULT 0);
      CREATE INDEX IF NOT EXISTS browse_signature ON browse_queries(signature,updated);
      CREATE TABLE IF NOT EXISTS browse_entries (
        snapshot TEXT, id TEXT, position INTEGER, payload TEXT, PRIMARY KEY(snapshot,id));
      CREATE INDEX IF NOT EXISTS browse_position ON browse_entries(snapshot,position);
        """)


initialize_storage()


def meta(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    value = json.loads(row[0]) if row else default
    if key == "collections" and isinstance(value, list):
        # MRKT identifies non-upgraded collections by their numeric gift ID.
        value = [c for c in value if not str(c.get("name", "")).isdigit() and c.get("giftType") != "Regular"]
    return value


def save_meta(key, value):
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))


def refresh_catalog_fx():
    response = requests.get("https://api.coingecko.com/api/v3/simple/price",
                            params={"ids": "the-open-network", "vs_currencies": "rub",
                                    "include_last_updated_at": "true"}, timeout=15)
    response.raise_for_status()
    quote = response.json()["the-open-network"]
    rate = float(quote["rub"])
    source_at = float(quote["last_updated_at"])
    now = time.time()
    if not 0 < rate < float("inf") or not -60 <= now - source_at < 900:
        raise ValueError("Invalid or stale TON/RUB quote")
    save_meta("catalog_fx", {"rate": str(rate), "fetched_at": now,
                             "source_at": source_at, "source": "CoinGecko"})


def catalog_fx():
    candidates = [meta("catalog_fx", {})]
    try:
        candidates.append((collector.main or MainSettings()).get("fx", "ton-rub"))
    except Exception:
        pass
    valid = []
    for quote in candidates:
        try:
            if float(quote.get("rate", 0)) > 0 and -60 <= time.time() - float(quote.get("fetched_at", 0)) < 900:
                valid.append(quote)
        except (ValueError, TypeError):
            continue
    return max(valid, key=lambda quote: float(quote["fetched_at"]), default={})


def run_catalog_fx(stop):
    while not stop.is_set():
        try:
            refresh_catalog_fx()
            delay = 300
        except Exception:
            delay = 60  # Keep the last valid quote through transient provider failures.
        stop.wait(delay)


def media_key(conn, key):
    if not isinstance(key, str) or not re.fullmatch(r"gifts/[A-Za-z0-9/_-]+\.webp", key):
        return None
    digest = hashlib.sha256(key.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO media VALUES (?,?)", (digest, key))
    return "/media/" + digest


def color(value, fallback):
    return f"#{value:06x}" if isinstance(value, int) and 0 <= value <= 0xffffff else fallback


def normalize(conn, gift, checked):
    return {"id": str(gift["id"]), "slug": gift.get("name", ""),
            "collection": gift.get("collectionName", ""), "collection_label": gift.get("collectionTitle") or gift.get("collectionName", ""), "model": gift.get("modelName", ""),
            "backdrop": gift.get("backdropName", ""), "symbol": gift.get("symbolName", ""),
            "number": int(gift.get("number") or 0), "price_nano": int(gift["salePrice"]),
            "price_ton": str(Decimal(str(gift["salePrice"])) / Decimal(10**9)),
            "image": media_key(conn, gift.get("modelStickerThumbnailKey")),
            "pattern": media_key(conn, gift.get("symbolStickerThumbnailKey")),
            "center": color(gift.get("backdropColorsCenterColor"), "#48675d"),
            "edge": color(gift.get("backdropColorsEdgeColor"), "#243d38"),
            "rarity": gift.get("modelRarityPerMille"), "checked_at": checked}


def search_payload(collection, count=60, **extra):
    return {"collectionNames": [collection], "modelNames": [], "backdropNames": [],
            "symbolNames": [], "ordering": "Price", "lowToHigh": True, "maxPrice": None,
            "minPrice": None, "mintable": None, "number": None, "count": count,
            "cursor": "", "query": None, "promotedFirst": False, "isTransferable": True,
            "isPremarket": False, "giftType": "Upgraded", **extra}


class Collector:
    def __init__(self):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.main = None
        self.client = None
        self.error = None
        self.active = None

    def request(self, method, path, payload=None):
        with self.lock:
            if self.client is None:
                self.main = MainSettings()
                self.client = Mrkt(self.main)
            return self.client.request(method, path, payload)

    def run(self):
        while not self.stop.is_set():
            try:
                collections = meta("collections", [])
                if not collections or time.time() - meta("collections_at", 0) > INTERVAL:
                    raw = self.request("GET", "/gifts/collections")
                    if not isinstance(raw, list):
                        raise ValueError("Invalid collections")
                    with connect() as conn:
                        collections = [{"name": c["name"], "title": c.get("title") or c["name"], "image": media_key(conn, c.get("thumbnailImageKey") or c.get("originalImageKey")),
                                        "floor_ton": str(Decimal(str(c.get("floorPriceNanoTons") or 0)) / Decimal(10**9))}
                                       for c in raw if c.get("name") and not c.get("isHidden") and not str(c["name"]).isdigit()]
                    save_meta("collections", collections)
                    save_meta("collections_at", time.time())
                save_meta("selected", [c["name"] for c in collections])
                prefetcher.warm_collections()
                # Slowly warm model dictionaries so text search does not depend
                # on which offer pages have already been visited.
                for c in collections:
                    if self.stop.is_set():
                        break
                    name = c["name"]
                    known = meta("models:" + name, {})
                    if time.time() - known.get("at", 0) < 86400:
                        continue
                    self.active = name
                    raw_models = self.request("POST", "/gifts/models", {"collections": [name]})
                    if isinstance(raw_models, list):
                        save_meta("models:" + name, {"at": time.time(), "items": sorted({m["modelName"] for m in raw_models if m.get("modelName")})})
                    if self.stop.wait(1):
                        break
                self.error = None
                self.active = None
                self.stop.wait(3600)
            except Exception:
                # Never expose upstream responses, headers or Telegram session details.
                self.error = "MRKT временно недоступен. Сохранённые предложения остаются в каталоге."
                self.active = None
                self.stop.wait(30)


collector = Collector()


def filter_values(value):
    if not value:
        return []
    try:
        values = json.loads(value) if value.startswith("[") else [value]
    except (ValueError, AttributeError):
        raise HTTPException(422, "Некорректный фильтр")
    if not isinstance(values, list) or len(values) > 30 or any(not isinstance(v, str) or not v or len(v) > 240 for v in values):
        raise HTTPException(422, "Можно выбрать до 30 вариантов одного фильтра")
    return sorted(set(values))


MODEL_SEPARATOR = "::model::"


def grouped_models(collections, model):
    groups, plain = {}, []
    for value in filter_values(model):
        if MODEL_SEPARATOR in value:
            owner, name = value.split(MODEL_SEPARATOR, 1)
            if owner not in collections or not name:
                raise HTTPException(422, "Модель не принадлежит выбранной коллекции")
            groups.setdefault(owner, set()).add(name)
        else:
            plain.append(value)
    if len(collections) > 1 and plain:
        # Keep existing links working; new UI sends explicit collection ownership.
        for owner in collections:
            known = set(meta("models:" + owner, {}).get("items", []))
            with connect() as conn:
                known.update(row[0] for row in conn.execute("SELECT DISTINCT model FROM gifts WHERE collection=?", (owner,)))
            matches = set(plain) & known
            if matches:
                groups.setdefault(owner, set()).update(matches)
        if groups:
            plain = []
    return groups, plain


def canonical_query(params):
    """A collection search and clicking that collection share one cache."""
    result = dict(params)
    for key in ("collection", "model", "backdrop", "symbol"):
        values = filter_values(result[key])
        result[key] = values[0] if len(values) == 1 else json.dumps(values, ensure_ascii=False, separators=(",", ":")) if values else ""
    text = result["q"].strip().casefold()
    names = [c["name"] for c in meta("collections", []) if text and (text in c["name"].casefold() or text in c.get("title", "").casefold())]
    if len(names) == 1 and (not result["collection"] or result["collection"] == names[0]):
        result.update(q="", collection=names[0])
    return result


def query_signature(params):
    return hashlib.sha256(json.dumps({"market_version": 4, "q": params["q"], "collection": params["collection"], "model": params["model"],
        "backdrop": params["backdrop"], "symbol": params["symbol"], "sort": params["sort"],
        "min": params["min_price"], "max": params["max_price"]}, sort_keys=True).encode()).hexdigest()


def default_query(**extra):
    return {"q": "", "collection": "", "model": "", "backdrop": "", "symbol": "", "sort": "price_asc",
            "min_price": None, "max_price": None, **extra}


class Prefetcher:
    """One bounded upstream queue; HTTP cache reads never enter this queue."""
    def __init__(self):
        self.tasks = queue.PriorityQueue()
        self.guard = threading.Lock()
        self.pending = {}
        self.errors = {}
        self.counter = 0
        self.stop = threading.Event()

    def enqueue(self, params, snapshot=None, offset=0, limit=20, priority=0):
        signature = query_signature(params)
        key = (signature, snapshot, offset, limit)
        with self.guard:
            error = self.errors.get(signature)
            if (key in self.pending and self.pending[key] <= priority) or (error and time.time() - error[0] < 10):
                return
            if key not in self.pending and (len(self.pending) >= 340 or (len(self.pending) >= 300 and priority >= 5)):
                return
            self.pending[key] = priority
            self.counter += 1
            self.tasks.put((priority, self.counter, key, dict(params)))

    def run(self):
        while not self.stop.is_set():
            try:
                priority, _, key, params = self.tasks.get(timeout=0.5)
            except queue.Empty:
                continue
            with self.guard:
                promoted = self.pending.get(key) != priority
            if promoted:
                self.tasks.task_done()
                continue
            signature, snapshot, offset, limit = key
            try:
                result = fill_browse(**params, snapshot=snapshot, offset=offset, limit=limit, max_pages=1)
                for gift in result.get("items", []):
                    preview_warmer.enqueue(gift.get("image"))
                with self.guard:
                    self.errors.pop(signature, None)
                # One upstream page per job keeps cold searches ahead of warming.
                if limit > 1 and result.get("snapshot") and offset == 0 and result["next_offset"] is not None:
                    warm_target = 120 if not params["collection"] and not params["q"] else 60
                    for position in range(20, warm_target, 20):
                        self.enqueue(params, result["snapshot"], position, 20, priority=25 if priority >= 20 else 5)
            except Exception:
                with self.guard:
                    self.errors[signature] = (time.time(), "MRKT временно недоступен. Повторяем загрузку в фоне.")
            finally:
                with self.guard:
                    if self.pending.get(key) == priority:
                        self.pending.pop(key, None)
                self.tasks.task_done()

    def warm_collections(self):
        self.enqueue(default_query(sort="price_asc"), limit=20, priority=1)
        for c in meta("collections", []):
            self.enqueue(default_query(collection=c["name"], sort="price_asc"), limit=20, priority=20)


prefetcher = Prefetcher()


class PreviewWarmer:
    def __init__(self):
        self.tasks = queue.Queue(maxsize=120)
        self.guard = threading.Lock()
        self.pending = set()

    def enqueue(self, url):
        if not url or not re.fullmatch(r"/media/[a-f0-9]{64}", url):
            return
        image_id = url.rsplit("/", 1)[1]
        if (DATA / "images" / (image_id + ".webp")).exists():
            return
        with self.guard:
            if image_id in self.pending or self.tasks.full():
                return
            self.pending.add(image_id)
            self.tasks.put_nowait(image_id)

    def run(self):
        while not prefetcher.stop.is_set():
            try:
                image_id = self.tasks.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                image_file(image_id)
            except Exception:
                pass  # A failed speculative preview never blocks the catalogue.
            finally:
                with self.guard:
                    self.pending.discard(image_id)
                self.tasks.task_done()


preview_warmer = PreviewWarmer()


def adopt_collection_snapshots():
    """Reuse snapshots saved before collection-name searches shared a cache."""
    with connect() as conn:
        rows = conn.execute("""SELECT q.id,m.value FROM browse_queries q
            JOIN meta m ON m.key='browse_payload:'||q.id""").fetchall()
        for row in rows:
            p = json.loads(row["value"])
            if p.get("giftType") != "Upgraded":
                continue
            names = p.get("collectionNames", [])
            if len(names) != 1 or any(p.get(k) for k in ("modelNames", "backdropNames", "symbolNames", "number", "minPrice", "maxPrice", "query")):
                continue
            order = "newest" if p.get("ordering") == "None" else "number" if p.get("ordering") == "Number" else "price_asc"
            if order != "newest" and not p.get("lowToHigh", True):
                order = "number_desc" if order == "number" else "price_desc"
            conn.execute("UPDATE browse_queries SET signature=? WHERE id=?", (query_signature(default_query(collection=names[0], sort=order)), row["id"]))


@asynccontextmanager
async def lifespan(app):
    with process_lock(DATA / "collector.lock"):
        with connect() as conn:
            conn.execute("DELETE FROM gifts WHERE collection != '' AND collection NOT GLOB '*[^0-9]*'")
        adopt_collection_snapshots()
        thread = threading.Thread(target=collector.run, daemon=True, name="catalog-collector")
        thread.start()
        fetch_thread = threading.Thread(target=prefetcher.run, daemon=True, name="catalog-prefetch")
        fetch_thread.start()
        for index in range(3):
            threading.Thread(target=preview_warmer.run, daemon=True, name=f"preview-warm-{index}").start()
        prefetcher.warm_collections()
        fx_stop = threading.Event()
        threading.Thread(target=run_catalog_fx, args=(fx_stop,), daemon=True,
                         name="catalog-fx").start()
        yield
        fx_stop.set()
        prefetcher.stop.set()
        collector.stop.set()
        thread.join(timeout=3)
        fetch_thread.join(timeout=3)


app = FastAPI(title="Giftroom · MRKT catalog", lifespan=lifespan, docs_url=None, redoc_url=None,openapi_url=None)


def public_origin():
    from backend.app.catalog_messages import validate_url
    try:return validate_url(MainSettings().settings().get("catalog_public_url", "")) or ""
    except (RuntimeError,sqlite3.Error):return ""


def client_ip(request,configured=None):
    peer=request.client.host if request.client else "unknown"
    configured=public_origin() if configured is None else configured
    expected=urlsplit(configured).hostname
    trusted={v.strip() for v in os.environ.get("CATALOG_TRUSTED_PROXY","").split(",") if v.strip()}
    if peer in trusted and expected and request.url.hostname==expected:
        try:return str(ipaddress.ip_address(request.headers.get("x-real-ip","")))
        except ValueError:pass
    if peer in {"127.0.0.1","::1"} and (expected or "").endswith(".trycloudflare.com") and request.url.hostname==expected:
        try:return str(ipaddress.ip_address(request.headers.get("cf-connecting-ip","")))
        except ValueError:pass
    return peer


def request_budget(request):
    path=request.url.path
    if not path.startswith("/api/") or path=="/api/health":return
    group="checkout" if request.method=="POST" and path.startswith("/api/checkout/") else "write" if request.method!="GET" else "read"
    limit={"checkout":20,"write":60,"read":600}[group]
    peer=client_ip(request)
    now=time.time()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for identity,maximum in [(peer,limit),("global",limit*2)]:
            key=hashlib.sha256((group+":"+identity).encode()).hexdigest()
            row=conn.execute("SELECT count,expires FROM request_budgets WHERE id=?",(key,)).fetchone()
            count,expires=(row[0],row[1]) if row and row[1]>now else (0,now+60)
            if count>=maximum:raise HTTPException(429,"Слишком много запросов. Попробуйте через минуту.",headers={"Retry-After":"60"})
            conn.execute("INSERT OR REPLACE INTO request_budgets VALUES (?,?,?)",(key,count+1,expires))
        conn.execute("DELETE FROM request_budgets WHERE expires<?",(now-3600,))


def checkout_visitor(request,configured):
    token=request.cookies.get("catalog_visitor","")
    digest=hashlib.sha256(token.encode()).hexdigest()
    fresh=False;now=time.time()
    with connect() as conn:
        row=conn.execute("SELECT expires FROM visitors WHERE digest=?",(digest,)).fetchone()
        if not row or row[0]<=now:
            token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest();fresh=True
            conn.execute("INSERT INTO visitors VALUES (?,?)",(digest,now+30*86400))
        conn.execute("DELETE FROM visitors WHERE expires<?",(now,))
    peer=client_ip(request,configured)
    return ("visitor:"+digest,"ip:"+hashlib.sha256(peer.encode()).hexdigest()),token if fresh else None


@app.middleware("http")
async def headers(request: Request, call_next):
    configured=await anyio.to_thread.run_sync(public_origin)
    allowed_hosts={"127.0.0.1","localhost","testserver","::1"}
    if configured:allowed_hosts.add(urlsplit(configured).hostname)
    if request.url.hostname not in allowed_hosts:
        return JSONResponse({"detail":"Недопустимый адрес каталога"},status_code=403)
    if request.method not in {"GET","HEAD"}:
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site")=="cross-site" or (origin and origin not in {"http://127.0.0.1:3180", "http://localhost:3180",configured}):
            return JSONResponse({"detail": "Origin rejected"}, status_code=403)
        if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length","0"))>8192:
            return JSONResponse({"detail":"Запрос слишком большой"},status_code=413)
    try:await anyio.to_thread.run_sync(request_budget,request)
    except HTTPException as exc:return JSONResponse({"detail":exc.detail},status_code=exc.status_code,headers=exc.headers)
    visitor_token=None
    if request.url.path=="/" or (request.method=="POST" and request.url.path.startswith("/api/checkout/")):
        request.state.checkout_owners,visitor_token=await anyio.to_thread.run_sync(checkout_visitor,request,configured)
    response = await call_next(request)
    if request.url.path.startswith(("/api/browse", "/api/gifts", "/api/gift/", "/api/filters")) and response.status_code==200 and "application/json" in response.headers.get("content-type",""):
        body=b"".join([part async for part in response.body_iterator])
        def decorate():
            value=json.loads(body)
            try:c=pricing_context(collector.main or MainSettings())
            except Exception:c=None
            def walk(node):
                if isinstance(node,list):
                    for item in node:walk(item)
                elif isinstance(node,dict):
                    for item in list(node.values()):walk(item)
                    for source,target in (("price_ton","price_rub"),("floor_price_ton","floor_price_rub")):
                        if source in node:
                            node[target]=None
                            if c and D(node[source])>0:
                                node[target]=calculate(D(node[source])*c["rate"],c["transfer"],c["profile"],c["strategy"],promotion_rub=c["premium"],ton_rub=c["rate"])["sale_price"]
            walk(value)
            return value
        value=await anyio.to_thread.run_sync(decorate)
        response=JSONResponse(value,status_code=response.status_code,headers={k:v for k,v in response.headers.items() if k.lower()!="content-length"})
    if request.url.path.startswith("/api/") and "application/json" in response.headers.get("content-type",""):
        from public_messages import public_data
        body=response.body if hasattr(response,"body") else b"".join([part async for part in response.body_iterator])
        response=JSONResponse(public_data(json.loads(body)),status_code=response.status_code,headers={k:v for k,v in response.headers.items() if k.lower()!="content-length"})
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https://nft.fragment.com; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' https://nft.fragment.com; frame-ancestors 'none'; base-uri 'self'"
    if request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/animation/", "/api/gift-image/")):
        response.headers["Cache-Control"] = "no-store"
    if visitor_token:
        secure=request.url.scheme=="https" or (configured.startswith("https://") and request.url.hostname==urlsplit(configured).hostname)
        response.set_cookie("catalog_visitor",visitor_token,httponly=True,secure=secure,samesite="lax",max_age=30*86400,path="/")
    return response


@app.get("/api/health")
def health():return {"status":"ok","name":"Playerok catalog"}


@app.get("/api/status")
def status():
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*),COUNT(DISTINCT collection),MIN(checked),MAX(checked) FROM gifts").fetchone()
    fx = catalog_fx()
    policy = public_policy(collector.main or MainSettings())
    if policy.get("ready"):fx={"rate":policy["rate"],"fetched_at":time.time(),"source":"Основная панель"}
    return {"count": meta("market_total", row[0]), "cached_count": row[0],
            "collections": len(meta("collections", [])) or row[1], "oldest_at": row[2], "updated_at": row[3],
            "collecting": collector.active, "error": "MRKT временно недоступен" if collector.error else None, "interval": INTERVAL,
            "stale_after": STALE_AFTER, "selected": meta("selected", []),
            "source": "MRKT", "demo": False, "pricing":policy, "fx": {k: fx.get(k) for k in ("rate", "fetched_at", "source")}}


browse_lock = threading.Lock()


def fill_browse(q: str = "", collection: str = "",
           model: str = "", backdrop: str = "", symbol: str = "", sort: str = "price_asc",
           min_price: float | None = None, max_price: float | None = None,
           offset: int = 0, limit: int = 20, snapshot: str | None = None, max_pages: int = 4):
    """Cache each upstream cursor stream separately to preserve pagination order."""
    search_collections = filter_values(collection)
    model_groups, search_models = grouped_models(search_collections, model)
    if model_groups:
        allowed_names = set().union(*model_groups.values())
        complete = True
        for owner in search_collections:
            if owner not in model_groups:
                dictionary = meta("models:" + owner, {}).get("items", [])
                if not dictionary:
                    complete = False
                    break
                allowed_names.update(dictionary)
        if complete:
            search_models = sorted(allowed_names)
    number = None
    text = q.strip().casefold().lstrip("#№").strip()
    if text:
        if text.isdigit():
            number = int(text)
            if number > 2_147_483_647:
                return {"items": [], "total": 0, "snapshot": None, "next_offset": None}
        else:
            names = [c["name"] for c in meta("collections", []) if text in c["name"].casefold() or text in c.get("title", "").casefold()]
            if collection:
                names = [name for name in names if name in search_collections]
            if names:
                search_collections = names
            else:
                with connect() as conn:
                    dictionaries = conn.execute("SELECT key,value FROM meta WHERE key LIKE 'models:%'").fetchall()
                    known = {r[0] for r in conn.execute("SELECT DISTINCT model FROM gifts")}
                for entry in dictionaries:
                    if not collection or entry["key"] in {"models:" + name for name in search_collections}:
                        known.update(json.loads(entry["value"]).get("items", []))
                matches = sorted(name for name in known if text in name.casefold())
                search_models = [name for name in matches if not model or model_groups or name in filter_values(model)]
                if not search_models:
                    if snapshot:
                        raise HTTPException(409, "Условия поиска изменились. Обновите каталог.")
                    return {"items": [], "total": 0, "snapshot": None, "next_offset": None}
    # MRKT silently ignores unknown free-text queries and returns the whole
    # market. Resolve text into explicit collection/model filters instead.
    payload = search_payload(collection, 60, collectionNames=search_collections,
        modelNames=search_models, backdropNames=filter_values(backdrop),
        symbolNames=filter_values(symbol), query=None, number=number,
        ordering="None" if sort == "newest" else "Number" if sort in {"number", "number_desc"} else "Price",
        lowToHigh=sort not in {"newest", "price_desc", "number_desc"},
        minPrice=int(Decimal(str(min_price))*10**9) if min_price is not None else None,
        maxPrice=int(Decimal(str(max_price))*10**9) if max_price is not None else None)
    signature = query_signature(default_query(q=q, collection=collection, model=model, backdrop=backdrop,
        symbol=symbol, sort=sort, min_price=min_price, max_price=max_price))
    with browse_lock:
        with connect() as conn:
            if snapshot:
                row = conn.execute("SELECT * FROM browse_queries WHERE id=? AND signature=?", (snapshot, signature)).fetchone()
                if not row:
                    raise HTTPException(409, "Список обновился. Обновите каталог.")
            else:
                row = conn.execute("SELECT * FROM browse_queries WHERE signature=? AND updated>? ORDER BY updated DESC LIMIT 1",
                                   (signature, time.time() - INTERVAL)).fetchone()
            if row is None:
                snapshot = uuid.uuid4().hex
                conn.execute("DELETE FROM browse_entries WHERE snapshot IN (SELECT id FROM browse_queries WHERE updated<?)", (time.time()-86400,))
                conn.execute("DELETE FROM browse_queries WHERE updated<?", (time.time()-86400,))
                conn.execute("INSERT INTO browse_queries VALUES (?,?,?,?,?,?)", (snapshot, signature, "", 0, time.time(), 0))
                conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("browse_payload:" + snapshot, json.dumps(payload)))
                row = conn.execute("SELECT * FROM browse_queries WHERE id=?", (snapshot,)).fetchone()
            snapshot = row["id"]
            cached = conn.execute("SELECT COUNT(*) FROM browse_entries WHERE snapshot=?", (snapshot,)).fetchone()[0]
            next_position = conn.execute("SELECT COALESCE(MAX(position)+1,0) FROM browse_entries WHERE snapshot=?", (snapshot,)).fetchone()[0]
            original = conn.execute("SELECT value FROM meta WHERE key=?", ("browse_payload:" + snapshot,)).fetchone()
            if original:
                payload = json.loads(original[0])
        cursor, total, exhausted = row["cursor"], row["total"], bool(row["exhausted"])
        # MRKT currently caps responses to 20, regardless of requested count.
        # Bound the work per HTTP request; continuation uses its real cursor.
        for _ in range(max_pages):
            if exhausted or cached >= offset + limit:
                break
            try:
                result = collector.request("POST", "/gifts/saling", {**payload, "cursor": cursor})
                if not isinstance(result, dict) or not isinstance(result.get("gifts"), list):
                    raise ValueError("Invalid response")
            except Exception:
                raise HTTPException(503, "MRKT временно недоступен. Нажмите «Показать ещё», чтобы повторить.") from None
            new_cursor = result.get("cursor") or ""
            exhausted = not new_cursor or new_cursor == cursor or not result["gifts"]
            total = int(result.get("total") or 0)
            checked = time.time()
            with connect() as conn:
                for gift in result["gifts"]:
                    allowed = model_groups.get(gift.get("collectionName"))
                    if allowed and gift.get("modelName") not in allowed:
                        continue
                    if gift.get("isSold") or not gift.get("isOnSale") or gift_block_reason(gift) or not gift.get("salePrice"):
                        continue
                    g = normalize(conn, gift, checked)
                    encoded = json.dumps(g)
                    inserted = conn.execute("INSERT OR IGNORE INTO browse_entries VALUES (?,?,?,?)", (snapshot, g["id"], next_position, encoded)).rowcount
                    if inserted:
                        cached += 1
                        next_position += 1
                    conn.execute("INSERT OR REPLACE INTO gifts VALUES (?,?,?,?,?,?,?,?,?)",
                        (g["id"], g["collection"], g["model"], g["backdrop"], g["symbol"], g["number"], g["price_nano"], checked, encoded))
                conn.execute("UPDATE browse_queries SET cursor=?,total=?,exhausted=? WHERE id=?", (new_cursor, total, int(exhausted), snapshot))
            cursor = new_cursor
            if not any((q, collection, model, backdrop, symbol, min_price is not None, max_price is not None)):
                save_meta("market_total", total)
        with connect() as conn:
            rows = conn.execute("SELECT payload FROM browse_entries WHERE snapshot=? ORDER BY position LIMIT ? OFFSET ?", (snapshot, limit, offset)).fetchall()
        values = [json.loads(r[0]) for r in rows]
        following = offset + len(values)
        return {"items": values, "total": total, "snapshot": snapshot,
                "next_offset": following if following < cached or not exhausted else None}


def search_is_known(params):
    text = params["q"].strip().casefold().lstrip("#№").strip()
    if not text:
        return True
    if text.isdigit():
        return int(text) <= 2_147_483_647
    if any(text in c["name"].casefold() or text in c.get("title", "").casefold() for c in meta("collections", [])):
        return True
    with connect() as conn:
        dictionaries = conn.execute("SELECT value FROM meta WHERE key LIKE 'models:%'").fetchall()
        known = [r[0] for r in conn.execute("SELECT DISTINCT model FROM gifts")]
    for row in dictionaries:
        known.extend(json.loads(row[0]).get("items", []))
    return any(text in name.casefold() for name in known)


@app.get("/api/browse")
async def browse(q: str = Query("", max_length=80), collection: str = Query("", max_length=4000),
           model: str = Query("", max_length=4000), backdrop: str = Query("", max_length=4000),
           symbol: str = Query("", max_length=4000), sort: str = "price_asc",
           min_price: float | None = Query(None, ge=0, le=1e9, allow_inf_nan=False),
           max_price: float | None = Query(None, ge=0, le=1e9, allow_inf_nan=False),
           offset: int = Query(0, ge=0, le=1_000_000), limit: int = Query(30, ge=1, le=60),
           snapshot: str | None = Query(None, max_length=32)):
    """Serve persisted pages immediately, even during a slow upstream request."""
    params = canonical_query(default_query(q=q, collection=collection, model=model, backdrop=backdrop,
        symbol=symbol, sort=sort, min_price=min_price, max_price=max_price))
    signature = query_signature(params)
    if not search_is_known(params):
        if snapshot:
            raise HTTPException(409, "Условия поиска изменились. Обновите каталог.")
        return {"items": [], "total": 0, "snapshot": None, "next_offset": None, "pending": False}

    def read_page():
        with connect() as conn:
            if snapshot:
                row = conn.execute("SELECT * FROM browse_queries WHERE id=? AND signature=?", (snapshot, signature)).fetchone()
                if not row:
                    raise HTTPException(409, "Список обновился. Обновите каталог.")
            else:
                # Prefer a usable old snapshot over an empty one being built.
                row = conn.execute("""SELECT * FROM browse_queries q WHERE signature=?
                    ORDER BY (exhausted OR EXISTS(SELECT 1 FROM browse_entries e WHERE e.snapshot=q.id)) DESC,
                    updated DESC LIMIT 1""", (signature,)).fetchone()
            if not row:
                return None, [], 0
            count = conn.execute("SELECT COUNT(*) FROM browse_entries WHERE snapshot=?", (row["id"],)).fetchone()[0]
            rows = conn.execute("SELECT payload FROM browse_entries WHERE snapshot=? ORDER BY position LIMIT ? OFFSET ?", (row["id"], limit, offset)).fetchall()
            return row, [json.loads(r[0]) for r in rows], count

    row, values, cached = read_page()
    if not row or (not values and not row["exhausted"]):
        prefetcher.enqueue(params, row["id"] if row else None, offset, 20, priority=0)
        # Usually unchanged; also permits synchronous workers in integration tests.
        row, values, cached = read_page()
    if row and not snapshot and time.time() - row["updated"] > INTERVAL:
        prefetcher.enqueue(params, priority=2)
    following = offset + len(values)
    more = not row or following < cached or not row["exhausted"]
    if row and more:
        # Keep roughly three screenfuls ahead without downloading the whole market.
        for position in range((cached // 20) * 20, following + 90, 20):
            prefetcher.enqueue(params, row["id"], position, 20, priority=5)
    error = prefetcher.errors.get(signature)
    if not values and error and time.time() - error[0] < 10:
        raise HTTPException(503, error[1])
    scoped_groups, _ = grouped_models(filter_values(params["collection"]), params["model"])
    return {"items": values, "total": row["total"] if row else 0,
            "total_is_upper_bound": bool(scoped_groups),
            "snapshot": row["id"] if row else None, "next_offset": following if more else None,
            "pending": not values and more, "stale": bool(row and time.time()-row["updated"] > INTERVAL)}


@app.get("/api/gifts")
def gifts(q: str = Query("", max_length=80), collection: str = "", model: str = "",
          backdrop: str = "", symbol: str = "", sort: str = "price_asc",
          min_price: float | None = Query(None, ge=0, le=1e9, allow_inf_nan=False),
          max_price: float | None = Query(None, ge=0, le=1e9, allow_inf_nan=False),
          offset: int = Query(0, ge=0, le=100000), limit: int = Query(30, ge=1, le=60),
          ids: str | None = Query(None, max_length=8000)):
    clauses, params = [], []
    selected_collections = filter_values(collection)
    model_groups, plain_models = grouped_models(selected_collections, model)
    for field, value in (("collection", collection), ("model", model), ("backdrop", backdrop), ("symbol", symbol)):
        if value and not (field == "model" and model_groups):
            values = filter_values(value)
            clauses.append(field + " IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
    if model_groups:
        branches = []
        for owner in selected_collections:
            allowed = sorted(model_groups.get(owner, []))
            branch = "collection=?"
            params.append(owner)
            if allowed:
                branch += " AND model IN (" + ",".join("?" for _ in allowed) + ")"
                params.extend(allowed)
            branches.append("(" + branch + ")")
        clauses.append("(" + " OR ".join(branches) + ")")
    if q.strip():
        text = q.strip().lstrip("#№").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(collection LIKE ? ESCAPE '\\' OR model LIKE ? ESCAPE '\\' OR CAST(number AS TEXT) LIKE ? ESCAPE '\\')")
        params += ["%" + text + "%"] * 3
    for op, value in ((">=", min_price), ("<=", max_price)):
        if value is not None:
            clauses.append("price" + op + "?")
            params.append(int(Decimal(str(value)) * 10**9))
    if ids is not None:
        values = [v for v in ids.split(",") if v][:200]
        if not values:
            clauses.append("0")
        else:
            clauses.append("id IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    order = {"price_asc": "price ASC,id", "price_desc": "price DESC,id", "number": "number ASC,id", "newest": "checked DESC,id", "recent": "checked DESC,id"}.get(sort, "price ASC,id")
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM gifts" + where, params).fetchone()[0]
        rows = conn.execute("SELECT payload FROM gifts" + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
    return {"items": [json.loads(r[0]) for r in rows], "total": total, "next_offset": offset + limit if offset + limit < total else None}


@app.get("/api/filters")
def filters(collection: str = ""):
    selected_collections = filter_values(collection)
    with connect() as conn:
        result = {}
        for field in ("collection", "model", "backdrop", "symbol"):
            where, args = (" WHERE collection IN (" + ",".join("?" for _ in selected_collections) + ")", selected_collections) if selected_collections and field != "collection" else ("", [])
            preview = "pattern" if field == "symbol" else "image"
            rows = conn.execute(f"""SELECT {field} AS name,COUNT(*) AS count,MIN(price) AS min_price_nano,
                MIN(json_extract(payload,'$.{preview}')) AS image,
                MIN(json_extract(payload,'$.rarity')) AS rarity,
                MIN(json_extract(payload,'$.center')) AS center,
                MIN(json_extract(payload,'$.edge')) AS edge
                FROM gifts{where} GROUP BY {field} ORDER BY {field}""", args).fetchall()
            result[field] = []
            for row in rows:
                if not row["name"]:
                    continue
                item = dict(row)
                item["price_ton"] = str(Decimal(item.pop("min_price_nano")) / Decimal(10**9))
                if field != "model":
                    item.pop("rarity", None)
                result[field].append(item)
    collections = meta("collections", [])
    images = {c["name"]: c["image"] for c in collections}
    if collections:
        result["collection"] = [{"name": c["name"], "title": c.get("title") or c["name"], "image": c["image"], **({"floor_price_ton": c["floor_ton"]} if Decimal(c.get("floor_ton") or "0") > 0 else {})} for c in sorted(collections, key=lambda c: c.get("title") or c["name"])]
    if selected_collections:
        details = {item["name"]: item for item in result["model"]}
        model_options = []
        titles = {c["name"]: c.get("title") or c["name"] for c in collections}
        for owner in selected_collections:
            names = set(meta("models:" + owner, {}).get("items", []))
            with connect() as conn:
                local = {row["model"]: json.loads(row["payload"]) for row in conn.execute("SELECT model,payload FROM gifts WHERE collection=? GROUP BY model", (owner,))}
            names.update(local)
            for name in sorted(names):
                if len(selected_collections) == 1:
                    model_options.append(details.get(name, {"name": name}))
                else:
                    gift = local.get(name, {})
                    model_options.append({"name": owner + MODEL_SEPARATOR + name, "title": name,
                        "collection": owner, "collection_label": titles.get(owner, owner),
                        "image": gift.get("image"), "rarity": gift.get("rarity")})
        if model_options:
            result["model"] = model_options
    for c in result["collection"]:
        c["image"] = images.get(c["name"])
    # A minimum in the local gifts table is not a market floor: the sample can
    # omit cheaper listings. Only use the first result of the exact ascending
    # MRKT query, with no extra model/price/search constraints.
    with connect() as conn:
        floors = {row["signature"]: json.loads(row["payload"]).get("price_ton") for row in conn.execute(
            """SELECT q.signature,e.payload FROM browse_queries q
               JOIN browse_entries e ON e.snapshot=q.id AND e.position=0
               WHERE q.updated>=? ORDER BY q.updated""", (time.time()-INTERVAL,))}
    for field, options in result.items():
        direct = meta(direct_floor_key(field, collection), {})
        if field == "backdrop" and direct.get("options"):
            result[field] = options = direct["options"]
        for option in options:
            params = default_query(collection=collection if field != "collection" else "")
            params[field] = option["name"]
            floor = floors.get(query_signature(params))
            if time.time()-direct.get("at",0) < INTERVAL and option["name"] in direct.get("floors",{}):
                floor = direct["floors"][option["name"]]
            if floor is not None:
                option["floor_price_ton"] = floor
    return result


floor_jobs = set()
floor_jobs_lock = threading.Lock()


def direct_floor_key(field, collection):
    return "mrkt-floors-v2:" + json.dumps([field, collection if field != "collection" else ""])


def warm_direct_floors(field, collection):
    key = direct_floor_key(field, collection)
    with floor_jobs_lock:
        if key in floor_jobs or len(floor_jobs) >= 8:
            return
        floor_jobs.add(key)
    def run():
        try:
            if field == "collection":
                raw = collector.request("GET", "/gifts/collections")
                name_field, price_field = "name", "floorPriceNanoTons"
            else:
                raw = collector.request("POST", "/gifts/" + ("models" if field == "model" else "backdrops"), {"collections": filter_values(collection)})
                name_field = "modelName" if field == "model" else "backdropName"
                price_field = "floorPriceNanoTons" if field == "model" else "floorNanoTons"
            if not isinstance(raw, list):
                return
            values = {}
            for item in raw:
                name, price = item.get(name_field), item.get(price_field)
                if name and field == "model" and len(filter_values(collection)) > 1:
                    name = item["collectionName"] + MODEL_SEPARATOR + name
                if not name:
                    continue
                value = str(Decimal(str(price))/Decimal(10**9)) if price and price > 0 else None
                if name not in values or (value is not None and (values[name] is None or Decimal(value)<Decimal(values[name]))):
                    values[name] = value
            cached = {"at": time.time(), "floors": values}
            if field == "backdrop":
                cached["options"] = sorted({item["backdropName"]: {"name": item["backdropName"],
                    "center": color(item.get("colorsCenterColor"), "#48675d"),
                    "edge": color(item.get("colorsEdgeColor"), "#243d38")}
                    for item in raw if item.get("backdropName")}.values(), key=lambda item: item["name"])
            save_meta(key, cached)
        finally:
            with floor_jobs_lock:
                floor_jobs.discard(key)
    def guarded():
        try:
            run()
        except Exception:
            pass
    threading.Thread(target=guarded, daemon=True, name="mrkt-floor-metadata").start()


@app.get("/api/filter-floors")
def filter_floors(field: str, collection: str = Query("", max_length=4000), names: list[str] = Query([])):
    if field not in {"collection", "model", "backdrop", "symbol"} or len(names) > 8 or any(not n or len(n) > 240 for n in names):
        raise HTTPException(422, "Invalid filter")
    if field == "model" and not collection:
        return {"floors": {}, "pending": False}
    if field == "collection" or (collection and field in {"model", "backdrop"}):
        cached = meta(direct_floor_key(field, collection), {})
        stale = time.time() - cached.get("at", 0) >= INTERVAL or (field == "backdrop" and "options" not in cached)
        if stale:
            warm_direct_floors(field, collection)
        return {"floors": {name: cached["floors"].get(name) for name in names} if "floors" in cached else {},
                "pending": stale, "source": "MRKT metadata", "options": cached.get("options")}
    return {"floors": {}, "pending": False, "source": "unsupported"}


check_lock=threading.Lock()


@app.post("/api/check/{gift_id}")
def check(gift_id: str):
    with check_lock:return check_one(gift_id)


def check_one(gift_id: str):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM gifts WHERE id=?", (gift_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Подарок больше не в выборке")
    gift = json.loads(row[0])
    if gift.get("verified_at",0)>time.time()-10:return {"available":True,"gift":gift,"changed":False}
    try:
        result = collector.request("POST", "/gifts/saling", search_payload(gift["collection"], 100, number=gift["number"]))
        if not isinstance(result, dict) or not isinstance(result.get("gifts"), list):
            raise ValueError("Invalid gifts")
        found = next((g for g in result["gifts"] if str(g.get("id")) == gift_id and g.get("isOnSale")
                      and not g.get("isSold") and not gift_block_reason(g)), None)
        with connect() as conn:
            if not found:
                conn.execute("DELETE FROM gifts WHERE id=?", (gift_id,))
                conn.execute("DELETE FROM browse_entries WHERE id=?", (gift_id,))
                return {"available": False, "checked_at": time.time()}
            value = normalize(conn, found, time.time())
            value["verified_at"]=time.time()
            conn.execute("UPDATE gifts SET price=?,checked=?,payload=? WHERE id=?", (value["price_nano"], value["checked_at"], json.dumps(value), gift_id))
            conn.execute("UPDATE browse_entries SET payload=? WHERE id=?", (json.dumps(value), gift_id))
        return {"available": True, "gift": value, "changed": gift["price_nano"] != value["price_nano"]}
    except Exception:
        raise HTTPException(503, "Не удалось проверить MRKT. Попробуйте чуть позже.") from None


image_slots = threading.BoundedSemaphore(6)
image_limiter = anyio.CapacityLimiter(6)


def image_file(image_id: str):
    if not re.fullmatch(r"[a-f0-9]{64}", image_id):
        raise HTTPException(404)
    path = DATA / "images" / (image_id + ".webp")
    if not path.exists():
        with connect() as conn:
            row = conn.execute("SELECT path FROM media WHERE id=?", (image_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        try:
            with image_slots:
                r = requests.get("https://cdn.tgmrkt.io/" + row[0], timeout=15, allow_redirects=False)
                r.raise_for_status()
                content = r.content
                if len(content) > 2_000_000 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
                    raise ValueError("Not a preview")
                # Unique temp path avoids concurrent readers observing partial writes.
                temp = path.with_suffix(f".{threading.get_ident()}.tmp")
                temp.write_bytes(content)
                temp.replace(path)
        except Exception:
            raise HTTPException(502, "Превью недоступно") from None
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public,max-age=604800,immutable"})


@app.get("/media/{image_id}")
async def image(image_id: str):
    # Slow CDN downloads must not consume every shared FastAPI worker thread.
    return await anyio.to_thread.run_sync(image_file, image_id, limiter=image_limiter)


def gift_image_file(gift_id: str):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM gifts WHERE id=?", (gift_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    slug = str(json.loads(row[0]).get("slug") or "").lower()
    if not re.fullmatch(r"[a-z0-9]+-[0-9]+", slug):
        raise HTTPException(404)
    digest = hashlib.sha256(("fragment/" + slug + ".medium.jpg").encode()).hexdigest()
    path = DATA / "images" / (digest + ".jpg")
    if not path.exists():
        try:
            response = requests.get("https://nft.fragment.com/gift/" + slug + ".medium.jpg", timeout=15, allow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 5_000_000 or not response.content.startswith(b"\xff\xd8"):
                raise ValueError("Invalid JPEG")
            temp = path.with_suffix(f".{threading.get_ident()}.tmp")
            temp.write_bytes(response.content)
            temp.replace(path)
        except Exception:
            raise HTTPException(502, "Изображение временно недоступно") from None
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public,max-age=604800,immutable"})


@app.get("/api/gift-image/{gift_id}")
async def gift_image(gift_id: str):
    return await anyio.to_thread.run_sync(gift_image_file,gift_id,limiter=image_limiter)


def animation_file(gift_id: str):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM gifts WHERE id=?", (gift_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        gift = json.loads(row[0])
    slug = str(gift.get("slug") or "").lower()
    if not re.fullmatch(r"[a-z0-9]+-[0-9]+", slug):
        raise HTTPException(404, "Анимация недоступна")
    key = "fragment/" + slug + ".lottie.json"
    digest = hashlib.sha256(key.encode()).hexdigest()
    path = DATA / "images" / (digest + ".json")
    if not path.exists():
        try:
            response = requests.get("https://nft.fragment.com/gift/" + slug + ".lottie.json", timeout=15, allow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ValueError("Too large")
            value = response.json()
            if not isinstance(value.get("layers"), list) or not value.get("w") or not value.get("h"):
                raise ValueError("Invalid animation")
            # Original gift vectors are self-contained; never load external assets.
            if any(asset.get("p") or asset.get("u") for asset in value.get("assets", [])):
                raise ValueError("External assets")
            temp = path.with_suffix(f".{threading.get_ident()}.tmp")
            temp.write_text(json.dumps(value), encoding="utf-8")
            temp.replace(path)
        except Exception:
            raise HTTPException(502, "Анимация временно недоступна") from None
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "public,max-age=604800,immutable"})




@app.get("/api/animation/{gift_id}")
async def animation(gift_id: str):
    return await anyio.to_thread.run_sync(animation_file,gift_id,limiter=image_limiter)


@app.get("/api/price")
def sale_price(ton: str = Query(..., max_length=40)):
    try:return catalog_sale_price(collector.main or MainSettings(),ton)
    except ValueError as exc:raise HTTPException(400,str(exc))



from pydantic import BaseModel, Field
class CheckoutInput(BaseModel):
    price_rub: int = Field(gt=0,le=10000000)

class PaidGiftInput(CheckoutInput):
    recipient: str = Field(min_length=5,max_length=33,pattern=r"^@?[A-Za-z][A-Za-z0-9_]{4,31}$")


class PaymentCodeInput(BaseModel):
    code: str = Field(min_length=1,max_length=40)


@app.post("/api/paid-choice/activate")
def activate_paid_choice(request: Request, payload: PaymentCodeInput):
    from backend.app import catalog_credit as credit
    db=Database(Config(ROOT))
    try:
        token=credit.activate(db,payload.code,client_ip(request))
        response=JSONResponse(credit.view(credit.session(db,token),db))
        secure=request.url.scheme=="https" or (public_origin().startswith("https://") and request.url.hostname==urlsplit(public_origin()).hostname)
        response.set_cookie("catalog_credit_session",token,httponly=True,samesite="lax",secure=secure,max_age=7*86400,path="/")
        return response
    except ValueError as exc:raise HTTPException(400,str(exc))
    finally:db.engine.dispose()


@app.get("/api/paid-choice")
def paid_choice(request: Request):
    from backend.app import catalog_credit as credit
    db=Database(Config(ROOT))
    try:return credit.view(credit.session(db,request.cookies.get("catalog_credit_session")),db)
    except ValueError as exc:raise HTTPException(401,str(exc))
    finally:db.engine.dispose()


@app.delete("/api/paid-choice")
def leave_paid_choice(request: Request):
    from backend.app import catalog_credit as credit
    db=Database(Config(ROOT))
    try:credit.end_session(db,request.cookies.get("catalog_credit_session"))
    finally:db.engine.dispose()
    response=JSONResponse({"ok":True})
    response.delete_cookie("catalog_credit_session",path="/")
    return response


@app.post("/api/paid-choice/select/{gift_id}")
def select_paid_gift(gift_id: str, payload: PaidGiftInput, request: Request):
    from backend.app import catalog_credit as credit
    db=Database(Config(ROOT))
    try:
        payment=credit.session(db,request.cookies.get("catalog_credit_session"))
        with connect() as conn:row=conn.execute("SELECT payload FROM gifts WHERE id=?",(gift_id,)).fetchone()
        if not row:raise HTTPException(404,"Подарок недоступен")
        gift=json.loads(row[0])
        if gift.get("unavailable"):raise ValueError("Подарок недоступен")
        return credit.choose(db,payment,gift,payload.price_rub,payload.recipient)
    except (ValueError,RuntimeError) as exc:raise HTTPException(400,str(exc))
    finally:db.engine.dispose()


@app.post("/api/paid-choice/test-topup")
def confirm_test_topup(request: Request):
    raise HTTPException(410,"Тестовые доплаты удалены. Оплатите настоящий лот Playerok")

@app.post("/api/checkout/{gift_id}")
def checkout(gift_id: str, payload: CheckoutInput, request: Request):
    with connect() as conn:
        row=conn.execute("SELECT payload FROM gifts WHERE id=?",(gift_id,)).fetchone()
    if not row:raise HTTPException(404,"Подарок недоступен")
    from backend.app.catalog_checkout import request_checkout
    db=Database(Config(ROOT))
    try:
        if request.cookies.get("catalog_credit_session"):
            from backend.app import catalog_credit as credit
            payment=credit.session(db,request.cookies["catalog_credit_session"])
            if (payment["state"]!="EXHAUSTED" and not payment.get("closed_after_delivery")) or credit.balance(payment)>0:
                raise ValueError("Применён баланс заказа. Используйте получение подарка по коду")
        key=request_checkout(db,json.loads(row[0]),payload.price_rub,owner_keys=request.state.checkout_owners)
        return {"id":key}
    except (ValueError,RuntimeError) as exc:raise HTTPException(400,str(exc))
    finally:db.engine.dispose()

@app.get("/api/checkout/{gift_id}")
def checkout_status(gift_id: str):
    db=Database(Config(ROOT))
    try:
        value=db.get("catalog_checkout",gift_id)
        if not value:raise HTTPException(404,"Запрос не найден")
        if value.get("state")=="PRICE_CHANGED" and value.get("updated_gift"):
            gift=value["updated_gift"]
            with connect() as conn:
                encoded=json.dumps(gift)
                conn.execute("UPDATE gifts SET price=?,checked=?,payload=? WHERE id=?",(gift["price_nano"],gift["checked_at"],encoded,gift_id))
                conn.execute("UPDATE browse_entries SET payload=? WHERE id=?",(encoded,gift_id))
        return {key:value.get(key) for key in ("state","url","price_rub","expires_at","error","updated_gift","notice","retry_at")}
    finally:db.engine.dispose()

app.mount("/", StaticFiles(directory=HERE / "web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3180, log_level="warning",proxy_headers=False)
