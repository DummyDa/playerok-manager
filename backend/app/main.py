from __future__ import annotations
import base64
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI,Request,Response,HTTPException,UploadFile,File,Form,Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select
from .config import Config
from .db import Database,Entity,Task,Login,uid
from . import schemas
from .auth import session,create_session,password_hash,password_valid
from .operations import new_operation,submit
from .safety import WriteBlocked,gate,is_nft_lot
from .pricing import D

def old_running():
    import requests
    try:
        r=requests.get("http://127.0.0.1:8000/api/health",timeout=1)
        return r.status_code==200 and r.json().get("name")=="Playerok Manager"
    except Exception:return False

def create_app(db:Database|None=None):
    db=db or Database(Config())
    @asynccontextmanager
    async def lifespan(app):
        # Each API boot gets a fresh check, regardless of the persisted interval.
        db.enqueue("check_updates",key="check_updates")
        db.put("clock","check_updates",{"next":time.time()+900})
        yield
    app=FastAPI(title="Playerok Manager 2",version="1.0.0",docs_url="/api/docs",openapi_url="/api/openapi.json",lifespan=lifespan)
    app.state.db=db
    from .connection_api import router as connection_router
    app.include_router(connection_router(db))
    from .updates import router as update_router
    app.include_router(update_router(db))
    public_origin=db.config.get("PUBLIC_ORIGIN").rstrip("/")
    public_url=urlsplit(public_origin)
    if public_origin and (public_url.scheme!="https" or not public_url.hostname or public_url.path or public_url.query or public_url.fragment or public_url.username):
        raise ValueError("PUBLIC_ORIGIN должен содержать только HTTPS-адрес панели")

    @app.middleware("http")
    async def protect(request:Request,call_next):
        path=request.url.path
        public={"/api/v1/health","/api/v1/auth/status","/api/v1/auth/setup","/api/v1/auth/login"}
        try:
            public_origin=db.config.get("PUBLIC_ORIGIN").rstrip("/")
            allowed_hosts={"localhost","127.0.0.1","testserver"}
            if public_origin:allowed_hosts.add(urlsplit(public_origin).hostname)
            if request.url.hostname not in allowed_hosts:raise HTTPException(400,"Недопустимый адрес сервера")
            if request.method in {"POST","PATCH","PUT","DELETE"}:
                origin=request.headers.get("origin")
                if origin:
                    p=urlsplit(origin)
                    local_origin=p.scheme=="http" and p.hostname in {"localhost","127.0.0.1","testserver"} and p.port in {None,3100,8101}
                    if not local_origin and not (public_origin and origin==public_origin):
                        raise HTTPException(403,"Недопустимый источник запроса")
                if request.headers.get("sec-fetch-site")=="cross-site":raise HTTPException(403,"Межсайтовый запрос запрещён")
            if path.startswith("/api/v1/") and path not in public:
                login=session(db,request)
                if request.method in {"POST","PATCH","PUT","DELETE"} and not secrets.compare_digest(request.headers.get("x-csrf-token",""),login.csrf):
                    raise HTTPException(403,"Обновите страницу: неверный CSRF-токен")
            return await call_next(request)
        except HTTPException as exc:return JSONResponse({"detail":exc.detail},status_code=exc.status_code)

    @app.exception_handler(ValueError)
    @app.exception_handler(WriteBlocked)
    async def bad_request(request,exc):return JSONResponse({"detail":db.redact(exc)},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request,exc):
        fields=[{"loc":e["loc"],"msg":e["msg"],"type":e["type"]} for e in exc.errors()]
        return JSONResponse({"detail":"Проверьте поля: "+"; ".join(str(e["loc"][-1])+" — "+e["msg"] for e in fields),"errors":fields},status_code=422)

    @app.get("/api/v1/health")
    def health()->dict:return {"status":"ok","name":"Playerok Manager 2"}

    @app.get("/api/v1/auth/status")
    def auth_status(request:Request)->dict:
        login=session(db,request,False)
        return {"initialized":bool(db.secret("password")),"authenticated":bool(login),"csrf":login.csrf if login else None}

    def login_response(response,password,request):
        token,csrf=create_session(db)
        configured_origin=db.config.get("PUBLIC_ORIGIN").rstrip("/")
        secure=bool(configured_origin and request.headers.get("origin")==configured_origin) or request.headers.get("x-forwarded-proto")=="https"
        response.set_cookie("manager_session",token,httponly=True,secure=secure,samesite="strict",max_age=86400,path="/")
        return {"authenticated":True,"csrf":csrf}

    @app.post("/api/v1/auth/setup")
    def setup(payload:schemas.Password,response:Response,request:Request)->dict:
        if db.secret("password"):raise HTTPException(409,"Пароль уже создан")
        # Unique primary key makes concurrent initial setup fail closed.
        from .db import Secret
        from sqlalchemy.exc import IntegrityError
        try:
            with db.tx() as s:s.add(Secret(name="password",value=db.cipher.encrypt(password_hash(payload.password).encode()).decode()))
        except IntegrityError:raise HTTPException(409,"Пароль уже создан")
        return login_response(response,payload.password,request)

    @app.post("/api/v1/auth/login")
    def login(payload:schemas.Password,response:Response,request:Request)->dict:
        key=request.client.host if request.client else "local"
        attempt=db.get("auth_attempt",key) or {}
        if attempt.get("blocked_until",0)>time.time():raise HTTPException(429,"Слишком много попыток входа; подождите минуту")
        if not password_valid(payload.password,db.secret("password") or ""):
            failures=attempt.get("failures",0)+1
            db.put("auth_attempt",key,{"failures":failures,"blocked_until":time.time()+60 if failures>=5 else 0})
            raise HTTPException(401,"Неверный пароль")
        db.put("auth_attempt",key,{"failures":0,"blocked_until":0})
        return login_response(response,payload.password,request)

    @app.post("/api/v1/auth/logout")
    def logout(request:Request,response:Response)->dict:
        login=session(db,request)
        with db.tx() as s:
            row=s.get(Login,login.digest)
            if row:s.delete(row)
        response.delete_cookie("manager_session",path="/");return {"ok":True}

    @app.post("/api/v1/auth/password")
    def change_password(payload:schemas.PasswordChange,request:Request)->dict:
        current=session(db,request)
        if not password_valid(payload.current_password,db.secret("password") or ""):raise HTTPException(400,"Текущий пароль неверен")
        db.secret("password",password_hash(payload.new_password))
        with db.tx() as s:
            for row in s.scalars(select(Login).where(Login.digest!=current.digest)):s.delete(row)
        db.log("admin_password","Пароль администратора изменён")
        return {"changed":True}

    @app.get("/api/v1/settings")
    def settings()->dict:
        result=db.settings();result.pop("bot_offset",None)
        return result

    @app.patch("/api/v1/settings")
    def update_settings(payload:schemas.Settings)->dict:
        data=payload.model_dump(exclude_unset=True,mode="json")
        if any(v is None for k,v in data.items() if k!="stars_rub"):raise ValueError("Пустое значение настройки")
        if data.get("notifications_enabled") and old_running():raise ValueError("Старый Playerok Manager запущен на порту 8000. Сначала остановите его, чтобы не запускать двух ботов.")
        if data.get("notifications_enabled") and (not db.config.get("TELEGRAM_BOT_TOKEN") or not (db.settings().get("owner_chat_id") or db.config.get("OWNER_TELEGRAM_USERNAME"))):raise ValueError("Сначала сохраните токен бота и username или Telegram ID владельца")
        if any(data.get(k) for k in ("repricing_enabled","purchases_enabled","transfers_enabled","promotions_enabled")) and old_running():raise ValueError("Перед включением автоматизации остановите старый проект: два исполнителя могут обработать один заказ.")
        if data.get("delivery_tested") and not db.settings()["delivery_tested"]:
            db.log("delivery_attestation","Владелец подтвердил отдельную проверку выдачи","Это подтверждение владельца, а не результат автоматического теста")
        db.configure(**data)
        if "quote_interval" in data:db.put("clock","quotes",{"next":time.time()})
        if "competitor_interval" in data:db.put("clock","competitors",{"next":time.time()})
        db.log("settings","Настройки обновлены",", ".join(data))
        return settings()

    @app.post("/api/v1/automation/stop")
    def stop()->dict:
        db.configure(emergency_stop=True);db.log("emergency_stop","Аварийная остановка")
        return settings()

    @app.get("/api/v1/connections")
    def connections()->dict:
        records={e["id"]:e for e in db.list("connection")}
        from .runtime_config import has_user_session
        configured={"playerok":bool(db.secret("playerok_cookies")),"mrkt":bool(has_user_session(db) and db.config.get("MRKT_TELEGRAM_API_ID") and db.config.get("MRKT_TELEGRAM_API_HASH")),"telegram":bool(db.config.get("TELEGRAM_BOT_TOKEN"))}
        return {"items":[{"id":name,"configured":available,**records.get(name,{"status":"NOT_CHECKED"})} for name,available in configured.items()],"worker":db.get("worker","main")}

    @app.post("/api/v1/connections/check")
    def check(payload:schemas.CheckInput)->dict:return {"task_id":db.enqueue("check",{"name":payload.name},key="check:"+payload.name)}

    @app.put("/api/v1/connections/playerok")
    def cookies(payload:schemas.Cookies)->dict:
        from .connection_api import ensure_connection_change_safe
        ensure_connection_change_safe(db)
        db.secret("playerok_cookies",payload.cookies.strip())
        db.put("connection","playerok",{"status":"NOT_CHECKED"})
        return {"task_id":db.enqueue("check",{"name":"playerok"},key="check:playerok")}

    @app.get("/api/v1/dashboard")
    def dashboard(start:float|None=None,end:float|None=None)->dict:
        lots=db.list("lot");orders=db.list("order");ledger=db.list("ledger",100000)
        ledger=[e for e in ledger if (start is None or e["updated_at"]>=start) and (end is None or e["updated_at"]<=end)]
        total=lambda kind:str(sum((D(e["amount_rub"]) for e in ledger if e.get("type")==kind),D(0)))
        revenue=D(total("revenue"));expenses=sum((D(total(k)) for k in ["purchase","transfer","promotion","fees"]),D(0))
        return {"active_lots":sum(l.get("status")=="APPROVED" and not l.get("missing") for l in lots),"inactive_lots":sum(l.get("status")!="APPROVED" for l in lots),
            "orders":len(orders),"problem_orders":sum(o.get("stage")=="MANUAL" for o in orders),"completed_orders":sum(o.get("stage")=="COMPLETE" for o in orders),
            "new_orders":sum(o.get("stage") in {"DISCOVERED","AWAITING_ENABLE","WAITING_DATA"} for o in orders),
            "executing_orders":sum(o.get("stage") in {"CHECK_PRICE","PURCHASING","PURCHASED","WITHDRAWN","TRANSFERRED"} for o in orders),
            "revenue_rub":str(revenue),"expenses_rub":str(expenses),"profit_rub":str(revenue-expenses),"connections":connections(),"fx":db.get("fx","ton-rub"),
            "settings":settings(),"events":db.list("event",10),"quote_count":len(db.list("quote"))}

    @app.get("/api/v1/lots")
    def lots()->dict:
        # Local edits update timestamps, but must never move items in the seller's list.
        records=sorted(db.list("lot"),key=lambda l:(bool(l.get("missing")),l.get("source_order",float("inf")),l["id"]))
        return {"items":[{**l,"delivery_mode":"telegram_nft" if is_nft_lot(l) else "manual","estimate":db.get("estimate",l["id"]) if is_nft_lot(l) else None,"pair":db.get("pair",l.get("pair_id",""))} for l in records]}

    @app.post("/api/v1/lots/sync")
    def sync_lots()->dict:return {"task_id":db.enqueue("sync_lots")}

    @app.get("/api/v1/lots/capabilities")
    def capabilities()->dict:return {"create":True,"edit":True,"premium":True,"quantity":False,"enable":False,"disable":False,"delete":False,
        "manual_reason":"Для количества, включения, отключения и удаления пока нет проверенного запроса площадки; используйте Playerok. Автоматическая имитация запрещена."}

    @app.get("/api/v1/lots/{id}")
    def lot(id:str)->dict:
        row=db.get("lot",id)
        if not row:raise HTTPException(404,"Лот не найден")
        return row

    @app.post("/api/v1/lots/{id}/refresh")
    def refresh_lot(id:str)->dict:return {"task_id":db.enqueue("refresh_lot",{"lot_id":id},key="refresh_lot:"+id)}

    @app.patch("/api/v1/lots/{id}/binding")
    def bind(id:str,payload:schemas.Binding)->dict:
        if not db.get("lot",id):raise HTTPException(404,"Лот не найден")
        if payload.delivery_mode=="manual":
            return db.put("lot",id,{"delivery_mode":"manual","pair_id":None})
        if not db.get("pair",payload.pair_id) or not db.get("fees",payload.fee_profile_id):raise ValueError("Не найдена пара или профиль комиссий")
        data=payload.model_dump(mode="json",exclude_none=True)
        result=db.put("lot",id,data);db.enqueue("quote",{"pair_id":payload.pair_id},key="quote:"+payload.pair_id)
        return result

    @app.post("/api/v1/lots/{id}/edit-preview")
    def edit_preview(id:str,payload:schemas.LotChange)->dict:
        if not db.get("lot",id):raise HTTPException(404,"Лот не найден")
        data=payload.model_dump(exclude_none=True)
        if not data:raise ValueError("Нет изменений")
        return new_operation(db,"update",{"lot_id":id,"changes":data})

    @app.post("/api/v1/lots/bulk-preview")
    def bulk(payload:schemas.BulkChange)->dict:
        return {"items":[edit_preview(id,payload.changes) for id in dict.fromkeys(payload.item_ids)]}

    @app.post("/api/v1/lots/create-preview")
    async def create_preview(title:str=Form(...),collection:str=Form(...),pair_id:str=Form(...),price:int=Form(...),description:str=Form(""),fee_profile_id:str=Form("default"),image:UploadFile=File(...))->dict:
        pair=db.get("pair",pair_id)
        if not pair or pair["collection"]!=collection:raise ValueError("Выберите точную коллекцию и модель")
        if not db.get("fees",fee_profile_id):raise ValueError("Профиль комиссий не найден")
        if price<1 or price>10000000:raise ValueError("Некорректная цена")
        content=await image.read(10*1024*1024+1)
        if len(content)>10*1024*1024:raise ValueError("Изображение больше 10 МБ")
        if not (content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff") or (content.startswith(b"RIFF") and content[8:12]==b"WEBP")):raise ValueError("Нужен PNG, JPEG или WebP")
        return new_operation(db,"create",{"fields":{"title":title,"collection":collection,"pair_id":pair_id,"fee_profile_id":fee_profile_id,"price":price,"description":description,"filename":image.filename},"image":base64.b64encode(content).decode()})

    @app.get("/api/v1/gift-pairs")
    def pairs()->dict:return {"items":db.list("pair"),"catalog":db.list("catalog")}

    @app.post("/api/v1/gift-pairs")
    def create_pair(payload:schemas.Pair)->dict:
        data=payload.model_dump(mode="json")
        data["collection"]=data["collection"].strip();data["model"]=data["model"].strip()
        if not data["collection"] or not data["model"]:raise ValueError("Заполните название и модель")
        key=hashlib.sha256((data["collection"].casefold()+"\0"+data["model"].casefold()).encode()).hexdigest()[:24]
        for old in db.list("pair"):
            if old["collection"].casefold()==data["collection"].casefold() and old["model"].casefold()==data["model"].casefold():return old
        row=db.put("pair",key,data);db.enqueue("quote",{"pair_id":key},key="quote:"+key);return row

    @app.post("/api/v1/gift-pairs/catalog")
    def catalog()->dict:return {"task_id":db.enqueue("catalog")}

    @app.get("/api/v1/gift-pairs/models")
    def models(collection:str)->dict:return {"items":[m for m in db.list("model",20000) if m.get("collection")==collection]}

    @app.post("/api/v1/gift-pairs/models")
    def fetch_models(payload:schemas.ModelsInput)->dict:return {"task_id":db.enqueue("models",{"collection":payload.collection},key="models:"+payload.collection)}

    @app.get("/api/v1/quotes")
    def quotes()->dict:return {"items":db.list("quote"),"history":db.list("quote_history",300),"fx":db.get("fx","ton-rub")}

    @app.post("/api/v1/quotes/refresh")
    def refresh_quote(payload:schemas.QuoteInput)->dict:
        if not db.get("pair",payload.pair_id):raise ValueError("Пара не найдена")
        return {"task_id":db.enqueue("quote",{"pair_id":payload.pair_id},key="quote:"+payload.pair_id)}

    @app.get("/api/v1/fee-profiles")
    def fees()->dict:return {"items":db.list("fees")}

    @app.put("/api/v1/fee-profiles/{id}")
    def fee(id:str,payload:schemas.FeeProfile)->dict:
        result=db.put("fees",id,payload.model_dump(mode="json"),False);db.enqueue("reprice");return result

    @app.get("/api/v1/pricing")
    def pricing()->dict:return {"strategy":db.get("strategy") or schemas.Strategy().model_dump(mode="json"),"items":[e for e in db.list("estimate") if is_nft_lot(db.get("lot",e["id"]))]}

    @app.put("/api/v1/pricing/strategy")
    def strategy(payload:schemas.Strategy)->dict:
        row=db.put("strategy","default",payload.model_dump(mode="json"),False);db.enqueue("reprice");return row

    @app.post("/api/v1/pricing/{id}/preview")
    def preview_price(id:str)->dict:
        # Configuration failures remain immediate; external tariff reads belong to the worker.
        from .service import Service
        from .pricing import PremiumQuoteRequired
        try:return Service(db).preview_price(id,allow_network=False)
        except PremiumQuoteRequired:return {"task_id":db.enqueue("preview_price",{"lot_id":id},key="preview_price:"+id)}

    @app.get("/api/v1/competitors")
    def competitors()->dict:return {"items":db.list("competitor")}

    @app.post("/api/v1/competitors")
    def competitor(payload:schemas.Competitor)->dict:
        url=urlsplit(payload.url)
        if url.scheme!="https" or url.hostname!="playerok.com" or not url.path.startswith("/products/"):raise ValueError("Нужна HTTPS-ссылка на объявление playerok.com")
        if not db.get("lot",payload.lot_id) or not db.get("pair",payload.pair_id):raise ValueError("Не найдены лот или пара")
        slug=url.path.removeprefix("/products/").strip("/")
        if "/" in slug or not slug:raise ValueError("Некорректная ссылка")
        key=hashlib.sha256((payload.lot_id+":"+slug).encode()).hexdigest()[:24]
        row=db.put("competitor",key,{**payload.model_dump(),"slug":slug})
        db.enqueue("competitor",{"id":key},key="competitor:"+key);return row

    @app.post("/api/v1/competitors/refresh")
    def refresh_competitors()->dict:return {"task_id":db.enqueue("competitors")}

    @app.put("/api/v1/competitors/{id}")
    def edit_competitor(id:str,payload:schemas.Competitor)->dict:
        if not db.get("competitor",id):raise HTTPException(404,"Конкурент не найден")
        # Reuse URL and ownership validation; keep the caller's persistent identity.
        validated=competitor(payload)
        if validated["id"]!=id:
            with db.tx() as s:
                temporary=s.get(Entity,("competitor",validated["id"]))
                if temporary:s.delete(temporary)
        row=db.put("competitor",id,{**payload.model_dump(),"slug":validated["slug"]})
        db.enqueue("competitor",{"id":id},key="competitor:"+id)
        return row

    @app.delete("/api/v1/competitors/{id}")
    def delete_competitor(id:str)->dict:
        with db.tx() as s:
            row=s.get(Entity,("competitor",id))
            if row:s.delete(row)
        return {"ok":True}

    @app.get("/api/v1/promotions")
    def promotions()->dict:return {"items":db.list("promotion_schedule"),"operations":[o for o in db.list("operation",300) if o.get("action")=="promotion"]}

    @app.put("/api/v1/promotions/{id}/schedule")
    def promotion_schedule(id:str,payload:schemas.PromotionSchedule)->dict:
        if not db.get("lot",id):raise ValueError("Лот не найден")
        result=db.put("promotion_schedule",id,{**payload.model_dump(mode="json"),"next_at":time.time()+payload.interval})
        if is_nft_lot(db.get("lot",id)):db.enqueue("reprice_preview")
        return result

    @app.put("/api/v1/promotions/schedules/bulk")
    def bulk_promotion_schedules(payload:schemas.BulkPromotionSchedule)->dict:
        ids=list(dict.fromkeys(payload.item_ids))
        values=payload.model_dump(mode="json",exclude={"item_ids"})
        now=time.time()
        with db.tx() as s:
            for id in ids:
                lot=s.get(Entity,("lot",id))
                if not lot or lot.data.get("missing") or lot.data.get("status")!="APPROVED":
                    raise ValueError("Выберите только действующие активные лоты; расписания не изменены")
            for id in ids:
                row=s.get(Entity,("promotion_schedule",id))
                data={**values,"next_at":now+payload.interval}
                if row:row.data={**row.data,**data};row.updated=now
                else:s.add(Entity(kind="promotion_schedule",id=id,data=data))
        db.log("promotion_schedules","Расписания поднятий сохранены",str(len(ids)))
        if any(is_nft_lot(db.get("lot",id)) for id in ids):db.enqueue("reprice_preview")
        return {"count":len(ids)}

    @app.delete("/api/v1/promotions/{id}/schedule")
    def delete_promotion_schedule(id:str)->dict:
        with db.tx() as s:
            row=s.get(Entity,("promotion_schedule",id))
            if row:s.delete(row)
        db.log("promotion_schedule_deleted","Расписание поднятий удалено",id)
        if is_nft_lot(db.get("lot",id)):db.enqueue("reprice_preview")
        return {"deleted":True}

    @app.post("/api/v1/promotions/{id}/preview")
    def preview_promotion(id:str)->dict:return {"task_id":db.enqueue("preview_promotion",{"lot_id":id},key="preview_promotion:"+id)}

    @app.post("/api/v1/operations/execute")
    def execute(payload:schemas.ExecuteInput)->dict:return submit(db,payload.operation_id)

    @app.get("/api/v1/operations/{id}")
    def operation(id:str)->dict:
        op=db.get("operation",id)
        if not op:raise HTTPException(404,"Операция не найдена")
        # Never send uploaded image blobs or Telegram access hashes back in histories.
        out=dict(op);out["payload"]={k:v for k,v in op.get("payload",{}).items() if k!="image"}
        return out

    @app.post("/api/v1/operations/{id}/reconcile")
    def reconcile(id:str)->dict:return {"task_id":db.enqueue("reconcile",{"operation_id":id},key="reconcile:"+id)}

    @app.post("/api/v1/operations/{id}/resolve")
    def resolve_operation(id:str,payload:schemas.OperationResolution)->dict:
        return {"task_id":db.enqueue("resolve_operation",{"operation_id":id,**payload.model_dump()},key="resolve:"+id)}

    @app.get("/api/v1/tasks/{id}")
    def task(id:str)->dict:
        with db.sessions() as s:
            t=s.get(Task,id)
            if not t:raise HTTPException(404,"Задание не найдено")
            return {"id":t.id,"kind":t.kind,"state":t.state,"attempts":t.attempts,"error":t.error,"result":t.result}

    @app.get("/api/v1/orders")
    def orders()->dict:
        return {"items":db.list("order"),"operations":[{k:v for k,v in o.items() if k!="payload"} for o in db.list("operation",300) if o.get("order_id")]}

    @app.post("/api/v1/orders/sync")
    def sync_orders()->dict:return {"task_id":db.enqueue("orders")}

    @app.patch("/api/v1/orders/{id}")
    def resolve(id:str,payload:schemas.ResolveOrder)->dict:
        order=db.get("order",id)
        if not order:raise ValueError("Заказ не найден")
        if order.get("stage")=="COMPLETE":raise ValueError("Завершённый заказ не меняется задним числом")
        return db.put("order",id,{**payload.model_dump(),"manual_hold":True,"cancel_requested":payload.stage=="CANCELLED"})

    @app.post("/api/v1/orders/{id}/process")
    def process_order(id:str)->dict:
        order=db.get("order",id)
        if not order or order.get("stage") in {"MANUAL","HISTORICAL","CANCELLED","COMPLETE"}:raise ValueError("Заказ требует ручной сверки; автоматический повтор запрещён")
        return {"task_id":db.enqueue("process_order",{"order_id":id},key="order:"+id)}

    @app.get("/api/v1/finance")
    def finance(start:float|None=None,end:float|None=None,lot_id:str|None=None,pair_id:str|None=None)->dict:
        records=[e for e in db.list("ledger",100000) if (start is None or e["updated_at"]>=start) and (end is None or e["updated_at"]<=end) and (not lot_id or e.get("lot_id")==lot_id)]
        if pair_id: records=[e for e in records if e.get("pair_id")==pair_id]
        sums={k:str(sum((D(e["amount_rub"]) for e in records if e.get("type")==k),D(0))) for k in ["revenue","purchase","transfer","promotion","fees"]}
        profit=D(sums["revenue"])-sum((D(sums[k]) for k in ["purchase","transfer","promotion","fees"]),D(0))
        groups={}
        for e in records:
            key=e.get("lot_id") or "other";groups[key]=groups.get(key,D(0))+(D(e["amount_rub"]) if e["type"]=="revenue" else -D(e["amount_rub"]))
        pair_groups={}
        for e in records:
            key=e.get("pair_id") or "other";pair_groups[key]=pair_groups.get(key,D(0))+(D(e["amount_rub"]) if e["type"]=="revenue" else -D(e["amount_rub"]))
        return {"items":records,"totals":sums,"profit_rub":str(profit),"by_lot":[{"lot_id":k,"profit_rub":str(v)} for k,v in groups.items()],
            "by_pair":[{"pair_id":k,"profit_rub":str(v)} for k,v in pair_groups.items()],"estimates":db.list("estimate")}

    @app.get("/api/v1/events")
    def events(q:str="",level:str="",limit:int=Query(200,ge=1,le=1000))->dict:
        return {"items":[e for e in db.list("event",10000) if (not q or q.casefold() in json.dumps(e,ensure_ascii=False).casefold()) and (not level or e.get("level")==level)][:limit]}

    @app.get("/api/v1/automation")
    def automation()->dict:
        with db.sessions() as s:
            tasks=[{"id":t.id,"kind":t.kind,"state":t.state,"due":t.due,"attempts":t.attempts,"error":t.error} for t in s.scalars(select(Task).order_by(Task.due.desc()).limit(100))]
        return {"worker":db.get("worker","main"),"tasks":tasks,"clocks":db.list("clock")}

    @app.post("/api/v1/automation/backup")
    def backup()->dict:return {"task_id":db.enqueue("backup")}
    return app

app=create_app()
