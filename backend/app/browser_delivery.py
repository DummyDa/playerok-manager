"""Local Chrome bridge. Browser clicks are never proof of delivery."""
import hashlib
import io
import re
import secrets
import time
import zipfile
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .db import Entity
from .pricing import D
from .safety import gate, is_nft_lot, ManualRequired, WriteBlocked

PREFIX = "/api/v1/browser-bridge/"
ACTIVE = {"QUEUED", "CLAIMED", "READY", "AUTHORIZED", "SUBMITTED", "VERIFYING"}

def status(db):
    row = db.get("browser", "chrome") or {}
    return {"paired": bool(db.secret("chrome_bridge_token")),
            "online": time.time() - row.get("heartbeat", 0) < 75,
            "ready": bool(row.get("ready")) and time.time() - row.get("heartbeat", 0) < 75,
            "heartbeat": row.get("heartbeat"), "error": row.get("error"),
            "extension_version": row.get("version"), "app_visible": row.get("app_visible"), "login_visible": row.get("login_visible"), "page_state":row.get("page_state"),
            "jobs": [{k: j.get(k) for k in ("id", "state", "order_id", "slug", "error", "updated_at")} for j in db.list("browser_job", 30)]}

def require_ready(db):
    if not status(db)["ready"]:
        raise ManualRequired("Расширение Chrome не готово: откройте Chrome и войдите в Telegram Web A")

def allowed(db, order_id):
    gate(db, "transfers")
    s = db.settings(); order = db.get("order", order_id)
    if s.get("delivery_transport") != "chrome_extension" or not s["delivery_tested"]:
        raise ManualRequired("Выдача через Chrome не включена или не проверена")
    if not order or not is_nft_lot(db.get("lot", order.get("lot_id"))) or order.get("manual_hold") or order.get("cancel_requested") or order.get("stage") in {"MANUAL", "CANCELLED", "COMPLETE"}:
        raise ManualRequired("Выдача заказа остановлена")
    return order

def enqueue(db, op):
    order = allowed(db, op["order_id"]); require_ready(db)
    slug = order["gift_slug"]
    if not re.fullmatch(r"[A-Za-z0-9]+-\d+", slug): raise ManualRequired("Нет точного slug NFT")
    recipient = order["recipient"].lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", recipient): raise ManualRequired("Нужен точный Telegram username получателя")
    existing = db.get("browser_job", op["id"])
    if existing: return existing
    saved = op["payload"]["saved"]
    job = db.put("browser_job", op["id"], {"state": "QUEUED", "order_id": order["id"], "slug": slug,
        "recipient": recipient, "recipient_id": order["recipient_id"], "stars": int(saved["transfer_stars"]),
        "created_at": time.time(), "deadline": time.time() + 300}, merge=False)
    db.put("operation", op["id"], {"state": "BROWSER_WAIT"})
    return job

def atomic(db, callback):
    with db.engine.connect() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            result = callback(c); c.commit(); return result
        except Exception:
            c.rollback(); raise

def update_row(c, row, **values):
    from sqlalchemy import update
    data = {**row.data, **values}
    c.execute(update(Entity).where(Entity.kind == "browser_job", Entity.id == row.id).values(data=data, updated=time.time()))
    return {"id": row.id, **data}

def transition(db, id, expected, **values):
    def change(c):
        from types import SimpleNamespace
        row=c.execute(select(Entity.id,Entity.data).where(Entity.kind=="browser_job",Entity.id==id)).first()
        if not row or row.data["state"] not in expected:return None
        return update_row(c,SimpleNamespace(id=row.id,data=row.data),**values)
    return atomic(db,change)

def claim(db):
    if db.settings().get("delivery_transport") != "chrome_extension": return None
    def transaction(c):
        rows = list(c.execute(select(Entity).where(Entity.kind == "browser_job")).mappings())
        if any(r["data"].get("state") in ACTIVE - {"QUEUED"} for r in rows): return None
        queued = sorted((r for r in rows if r["data"].get("state") == "QUEUED" and r["data"]["deadline"] > time.time()), key=lambda r: r["created"])
        if not queued: return None
        row = queued[0]
        if not row["data"].get("preview_only"):allowed(db,row["data"]["order_id"])
        from types import SimpleNamespace
        return update_row(c, SimpleNamespace(id=row["id"], data=row["data"]), state="CLAIMED", lease=secrets.token_urlsafe(24), deadline=time.time()+150)
    job = atomic(db, transaction)
    if job and not job.get("preview_only"):
        try: allowed(db, job["order_id"])
        except Exception:
            db.put("browser_job", job["id"], {"state": "FAILED", "error": "Заказ остановлен"}); return None
    return job

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
class BrowserPairing(Strict): code: str = Field(min_length=20, max_length=100)
class Poll(Strict):
    ready: bool = False
    error: str = Field(default="", max_length=300)
    version: str = Field(default="", max_length=30)
    app_visible: bool | None = None
    login_visible: bool | None = None
    page_state: str = Field(default="unknown",max_length=30)
class Event(Strict):
    job_id: str = Field(max_length=200)
    lease: str = Field(max_length=100)
    event: str = Field(pattern="^(prepared|submit|done|error)$")
    slug: str = Field(default="", max_length=100)
    recipient_id: int | None = None
    stars: int | None = Field(default=None, ge=0, le=100000)
    error: str = Field(default="", max_length=300)

def router(db):
    routes = APIRouter()
    def extension(request):
        origin = request.headers.get("origin", "")
        if request.client.host not in {"127.0.0.1", "::1", "testclient"} or request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            raise HTTPException(403, "Только локальное подключение")
        if not re.fullmatch(r"chrome-extension://[a-p]{32}", origin): raise HTTPException(403, "Требуется расширение Chrome")
        return origin
    def auth(request):
        origin = extension(request)
        key = db.secret("chrome_bridge_token")
        if not key or origin != (db.get("browser", "chrome") or {}).get("origin") or not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + key):
            raise HTTPException(401, "Расширение не подключено")
    @routes.get("/api/v1/browser-delivery")
    def read(): return status(db)
    @routes.post("/api/v1/browser-delivery/pairing")
    def pairing():
        code = secrets.token_urlsafe(24)
        db.put("browser_pair", "current", {"digest": hashlib.sha256(code.encode()).hexdigest(), "expires": time.time()+300})
        return {"code": code, "expires_in": 300}
    @routes.post("/api/v1/browser-delivery/revoke")
    def revoke():
        db.forget_secret("chrome_bridge_token")
        db.put("browser", "chrome", {"ready": False, "heartbeat": 0})
        return {"ok": True}
    @routes.get("/api/v1/browser-delivery/extension.zip")
    def download():
        root = db.config.root / "extension"; output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in root.iterdir():
                if file.is_file() and file.suffix in {".json", ".js", ".html", ".css"}: archive.writestr(file.name, file.read_bytes())
        return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="playerok-chrome-extension.zip"'})
    @routes.post(PREFIX + "pair")
    def pair(payload: BrowserPairing, request: Request):
        origin = extension(request)
        def consume(c):
            from sqlalchemy import delete
            row = c.execute(select(Entity.data).where(Entity.kind == "browser_pair", Entity.id == "current")).scalar_one_or_none()
            if not row or row["expires"] < time.time() or not secrets.compare_digest(row["digest"], hashlib.sha256(payload.code.encode()).hexdigest()): raise HTTPException(403, "Код подключения истёк или неверен")
            c.execute(delete(Entity).where(Entity.kind == "browser_pair", Entity.id == "current"))
        atomic(db, consume)
        if any(j["state"] in ACTIVE - {"QUEUED"} for j in db.list("browser_job")): raise HTTPException(409, "Сначала завершите текущую передачу")
        token = secrets.token_urlsafe(32); db.secret("chrome_bridge_token", token)
        db.put("browser", "chrome", {"origin": origin, "ready": False, "heartbeat": 0}, merge=False)
        return {"token": token}
    @routes.post(PREFIX + "poll")
    def poll(payload: Poll, request: Request):
        auth(request)
        db.put("browser", "chrome", {"heartbeat": time.time(), **payload.model_dump()})
        job = None
        if payload.ready:
            try: job = claim(db)
            except (ValueError, ManualRequired, WriteBlocked): pass
        return {"job": job}
    @routes.get(PREFIX + "job/{id:path}")
    def job(id: str, request: Request):
        auth(request); row = db.get("browser_job", id)
        if not row: raise HTTPException(404, "Задание не найдено")
        return {k: row.get(k) for k in ("id", "state", "permit_until", "error")}
    @routes.post(PREFIX + "event")
    def event(payload: Event, request: Request):
        auth(request)
        def change(c):
            from types import SimpleNamespace
            row = c.execute(select(Entity.id, Entity.data).where(Entity.kind == "browser_job", Entity.id == payload.job_id)).first()
            if not row or not secrets.compare_digest(row.data.get("lease", ""), payload.lease): raise HTTPException(403, "Задание не принадлежит расширению")
            row = SimpleNamespace(id=row.id, data=row.data); state = row.data["state"]
            if payload.event in {"prepared", "submit"}:
                if row.data.get("preview_only"):
                    if payload.event=="submit":raise HTTPException(409,"Тестовая передача подтверждается вручную в Telegram")
                else:allowed(db, row.data["order_id"])
            if payload.event == "prepared":
                if state != "CLAIMED" or row.data["deadline"] < time.time(): raise HTTPException(409, "Подготовка истекла")
                if payload.slug != row.data["slug"] or payload.recipient_id != row.data["recipient_id"] or payload.stars != row.data["stars"]: raise HTTPException(409, "NFT, получатель или стоимость не совпадают")
                update_row(c, row, state="READY",deadline=time.time()+900 if row.data.get("preview_only") else row.data["deadline"]); return {"accepted": True}
            if payload.event == "submit":
                if state != "AUTHORIZED" or row.data.get("permit_until", 0) < time.time(): raise HTTPException(409, "Разрешение истекло или уже использовано")
                update_row(c, row, state="SUBMITTED", submitted_at=time.time()); return {"permit": True}
            if state not in ACTIVE: return {"accepted": False}
            if row.data.get("preview_only") and state=="READY":return {"accepted":False}
            update_row(c, row, state="VERIFYING" if state in {"SUBMITTED", "VERIFYING"} else "FAILED", error=payload.error if payload.event == "error" else None)
            return {"accepted": True}
        try:return atomic(db, change)
        except ManualRequired as exc:raise HTTPException(409,str(exc))
    return routes

def tick(service):
    db = service.db
    for job in db.list("browser_job", 10000):
        state = job["state"]
        if state not in ACTIVE | {"FAILED"}: continue
        if job.get("preview_only"):
            if state=="READY":
                try:owner=service.mrkt.owner(job["slug"])
                except Exception:owner=None
                if owner==job["recipient_id"]:
                    transition(db,job["id"],{"READY"},state="COMPLETE",verified_owner=owner)
                    continue
            if time.time()>job["deadline"] or state=="FAILED":transition(db,job["id"],{state},state="CANCELLED")
            continue
        if state == "READY":
            try:
                allowed(db, job["order_id"])
                op = db.get("operation", job["id"]); service.order_preflight(op)
                # A username can be reassigned while the UI was open.
                async def resolve(c): return (await c.get_entity(job["recipient"])).id
                if service.mrkt.telegram(resolve) != job["recipient_id"]: raise ManualRequired("Изменился получатель Telegram")
                transition(db, job["id"], {"READY"}, state="AUTHORIZED", permit_until=time.time()+20, stars_rub=db.settings()["stars_rub"])
            except Exception:
                transition(db, job["id"], {"READY"}, state="FAILED", error="Повторная проверка передачи не пройдена")
            continue
        if state in {"SUBMITTED", "VERIFYING"}:
            try: owner = service.mrkt.owner(job["slug"])
            except Exception: owner = None
            if owner == job["recipient_id"]:
                order = db.get("order", job["order_id"])
                # Keep the job reconcilable until all idempotent records exist.
                # A crash between ledger/order writes must not lose delivery.
                if not transition(db, job["id"], {"SUBMITTED", "VERIFYING"}, state="VERIFYING"):continue
                db.put("operation", job["id"], {"state": "COMPLETE", "reconciled": True, "result": {"slug": job["slug"], "recipient_id": owner}})
                service.ledger(job["id"], "transfer", D(job["stars"])*D(job.get("stars_rub",db.settings()["stars_rub"])), order["lot_id"], order["id"])
                if not order.get("manual_hold") and not order.get("cancel_requested") and order["stage"] not in {"MANUAL", "CANCELLED", "COMPLETE"}:
                    db.put("order", order["id"], {"stage": "TRANSFERRED"})
                    db.enqueue("process_order", {"order_id": order["id"]}, key="order:"+order["id"])
                transition(db, job["id"], {"VERIFYING"}, state="COMPLETE")
                continue
            if time.time() - job.get("submitted_at", time.time()) < 120: continue
        elif state != "FAILED" and time.time() < (job.get("permit_until", 0) if state == "AUTHORIZED" else job["deadline"]): continue
        uncertain = state in {"SUBMITTED", "VERIFYING"}
        reason = "Не подтверждён результат передачи. Повторная отправка запрещена" if uncertain else job.get("error") or "Время ожидания расширения Chrome истекло"
        if not transition(db, job["id"], {state}, state=state, error=reason):continue
        db.put("operation", job["id"], {"state": "UNKNOWN" if uncertain else "FAILED", "error": reason})
        db.put("order", job["order_id"], {"stage": "MANUAL", "reason": reason})
        service.notice("browser:"+job["id"], "Нужна проверка выдачи", reason, order_id=job["order_id"])
        transition(db, job["id"], {state}, state="UNKNOWN" if uncertain else "CANCELLED", error=reason)

def prepare_manual_preview(service,recipient):
    """Read-only account inspection, followed by a browser rehearsal without a payment permit."""
    from telethon import functions,types
    from .db import uid
    db=service.db;require_ready(db)
    if any(j["state"] in ACTIVE for j in db.list("browser_job")):raise ManualRequired("Уже есть активное задание Chrome")
    async def inspect(c):
        peer=await c.get_entity(recipient)
        if not isinstance(peer,types.User) or peer.bot or peer.deleted:raise ManualRequired("Нужен активный пользователь Telegram")
        me=await c.get_me()
        if peer.id==me.id:raise ManualRequired("Передача самому себе не поддерживается")
        reserved={o.get("gift_slug") for o in db.list("order",10000) if o.get("stage") not in {"COMPLETE","CANCELLED","HISTORICAL"}}
        page=await c(functions.payments.GetSavedStarGiftsRequest(peer=types.InputPeerSelf(),offset="",limit=100))
        choices=[]
        for saved in page.gifts:
            slug=getattr(saved.gift,"slug",None)
            at=getattr(saved,"can_transfer_at",None)
            at=at.timestamp() if hasattr(at,"timestamp") else at or 0
            if slug and slug not in reserved and at<=time.time():choices.append((int(getattr(saved,"transfer_stars",0) or 0),slug))
        if not choices:raise ManualRequired("Нет доступных NFT вне незавершённых заказов")
        stars,slug=min(choices)
        return {"slug":slug,"recipient":recipient.lstrip("@"),"recipient_id":peer.id,"stars":stars}
    data=service.mrkt.telegram(inspect)
    id="manual-preview:"+uid()
    db.put("browser_job",id,{**data,"state":"QUEUED","preview_only":True,"created_at":time.time(),"deadline":time.time()+300},merge=False)
    return {"job_id":id,**data}
