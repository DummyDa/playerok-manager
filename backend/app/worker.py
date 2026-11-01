from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import time
from sqlalchemy import select, update, case
from .config import Config
from .db import Database, Task
from .service import Service
from .safety import RateLimited
from .operations import recover
from .process_lock import process_lock as worker_lock

class Worker:
    def __init__(self, db):self.db=db;self.service=Service(db);self.stopped=False
    def schedule(self):
        from .telegram_login import expire
        from .runtime_config import has_user_session
        expire(self.db)
        s=self.db.settings()
        intervals={"check":300,"sync_lots":300,"fx":300,"quotes":30,"competitors":s["competitor_interval"],
                   "buyer_support":30,"grant_tick":15,"catalog_credit_tick":15,"catalog_expire":30,"orders":60,"bot":5,"promotions":30,"backup":86400,"check_updates":900,"browser_delivery":5}
        for kind,interval in intervals.items():
            if kind in {"sync_lots","orders","competitors","buyer_support"} and not self.db.secret("playerok_cookies"):continue
            if kind=="quotes" and not has_user_session(self.db):continue
            if kind=="bot" and not s["notifications_enabled"]:continue
            if kind=="promotions" and (s["emergency_stop"] or not s["promotions_enabled"]):continue
            record=self.db.get("clock",kind)
            if not record or record.get("next",0)<=time.time() or (kind=="quotes" and record.get("next",0)>time.time()+30):
                self.db.enqueue(kind)
                self.db.put("clock",kind,{"next":time.time()+interval})

    def claim(self):
        quote_ready=max((self.db.get("throttle","mrkt") or {}).get("next",0),(self.db.get("throttle","quote_queue") or {}).get("next",0))
        with self.db.tx() as s:
            s.execute(update(Task).where(Task.kind=="quote",Task.state=="READY",Task.due<quote_ready).values(due=quote_ready))
            task=s.scalar(select(Task).where(Task.state=="READY",Task.due<=time.time()).order_by(
                case((Task.kind=="telegram_auth",-1),(Task.kind.in_(["execute","buyer_message","catalog_message"]),0),(Task.kind.in_(["process_order","catalog_fulfill","catalog_expire"]),1),else_=2),Task.due).limit(1))
            if not task:return None
            affected=s.execute(update(Task).where(Task.id==task.id,Task.state=="READY").values(state="RUNNING",lease=time.time()+180,attempts=Task.attempts+1)).rowcount
            if not affected:return None
            claimed=task.id,task.kind,dict(task.payload)
        if claimed[1]=="quote":self.db.put("throttle","quote_queue",{"next":time.time()+3})
        return claimed

    def run_one(self):
        claimed=self.claim()
        if not claimed:return False
        id,kind,payload=claimed
        self.db.put("worker","main",{"status":"BUSY","task":kind,"heartbeat":time.time(),"pid":os.getpid()})
        try:
            result=self.dispatch(kind,payload)
            with self.db.tx() as s:
                task=s.get(Task,id);task.state="COMPLETE";task.dedupe=None;task.lease=None
                task.result=result if isinstance(result,dict) else {"ok":True};task.error=None
        except Exception as exc:
            error=self.db.redact(exc)
            waiting=kind=="process_order" and isinstance(exc,RateLimited)
            with self.db.tx() as s:
                task=s.get(Task,id);task.error=error;task.lease=None
                # Money tasks never retry blindly; all public write paths use operations.
                retry=waiting or (kind not in {"execute","process_order","telegram_auth","buyer_message","catalog_message","catalog_create","grant_message","grant_execute","grant_refund"} and task.attempts<4)
                task.state="READY" if retry else "FAILED"
                task.due=max(time.time()+1,exc.retry_at+0.5) if waiting else time.time()+min(300,10*2**min(task.attempts,8))
                if retry:
                    for service in ("playerok","mrkt"):
                        task.due=max(task.due,(self.db.get("throttle",service) or {}).get("next",0))
                    if kind=="quote":task.due=max(task.due,(self.db.get("quote",payload["pair_id"]) or {}).get("next_check_at",0))
                if not retry:task.dedupe=None
            if waiting:self.db.put("order",payload["order_id"],{"reason":"Ожидаем разрешённое время проверки. Повторно оплачивать не нужно."})
            if kind=="process_order" and not waiting:self.db.put("order",payload["order_id"],{"stage":"MANUAL","reason":error})
            if kind=="process_order" and not waiting:
                from .catalog_credit import failed_order
                failed_order(self.db,payload["order_id"],error)
                from .funded_orders import action_failure
                action_failure(self.db,payload["order_id"],error)
            self.db.log("task_error","Ошибка фоновой задачи",kind+": "+error,"ERROR")
        self.db.put("worker","main",{"status":"IDLE","task":None,"heartbeat":time.time()})
        return True

    def dispatch(self, kind,p):
        revision=(self.db.get("runtime_config","revision") or {}).get("updated_at")
        if revision!=getattr(self,"runtime_revision",None):
            from .integrations.clients import Mrkt
            self.service.mrkt=Mrkt(self.db);self.runtime_revision=revision
        if kind=="telegram_auth":
            from .telegram_login import step
            result=step(self.db,p["flow_id"],p["action"])
            self.service.mrkt.token=None
            return result
        if kind=="check_updates":
            from .updates import check
            return check(self.db)
        service=self.service
        if kind=="buyer_support":
            from .buyer_support import poll
            return poll(service)
        if kind=="catalog_message":
            from .catalog_messages import send
            return send(service,p["message_id"])
        if kind in {"catalog_fulfill","catalog_credit_tick"}:
            from .catalog_credit import fulfill,tick
            return fulfill(service,p["credit_id"]) if kind=="catalog_fulfill" else tick(service)
        if kind.startswith("grant_"):
            from . import funded_orders as funding
            if kind=="grant_tick":return funding.tick(service)
            if kind=="grant_execute":return funding.execute(service,p["grant_id"])
            if kind=="grant_message":return funding.send_message(service,p["message_id"])
            if kind=="grant_refund":return funding.refund(service,p["grant_id"],p.get("payment_id"))
        if kind in {"catalog_create","catalog_expire"}:
            from .catalog_checkout import create,expire
            return create(service,p["checkout_id"]) if kind=="catalog_create" else expire(service)
        if kind=="buyer_message":
            from .buyer_messages import send
            return send(service,p["message_id"])
        if kind=="browser_manual_preview":
            from .browser_delivery import prepare_manual_preview
            return prepare_manual_preview(service,p["recipient"])
        if kind=="browser_delivery":
            from .browser_delivery import tick
            return tick(service)
        if kind=="reprice_preview":return service.reprice(apply=False)
        actions={"check":lambda:service.check(p.get("name","all")),"sync_lots":service.sync_lots,"fx":service.refresh_fx,
            "quotes":service.quotes,"quote":lambda:service.refresh_quote(p["pair_id"],scheduled=p.get("scheduled",False)),"catalog":service.catalog,"models":lambda:service.models(p["collection"]),
            "competitors":service.competitors,"competitor":lambda:service.competitor(p["id"]),"reprice":service.reprice,
            "preview_relist":lambda:service.preview_relist(p["lot_id"],p.get("premium",False)),
            "preview_price":lambda:service.preview_price(p["lot_id"]),"preview_promotion":lambda:service.preview_promotion(p["lot_id"]),
            "preview_resume_create":lambda:service.preview_resume_create(p["operation_id"]),
            "execute":lambda:service.execute(p["operation_id"]),"orders":service.orders,
            "manual_delivery_confirm":lambda:self.confirm_manual_delivery(service,p["order_id"]),
            "process_order":lambda:service.process_order(p["order_id"]),"reconcile":lambda:service.reconcile(p["operation_id"]),
            "resolve_operation":lambda:service.resolve_operation(p["operation_id"],p["resolution"],p["evidence"]),
            "bot":service.bot_poll,"promotions":service.schedules,"backup":service.backup,"refresh_lot":lambda:service.refresh_lot(p["lot_id"])}
        if kind not in actions:raise ValueError("Неизвестный тип задания")
        return actions[kind]()

    def confirm_manual_delivery(self,service,order_id):
        from .manual_delivery import confirm
        return confirm(service,order_id)

    def run(self):
        with worker_lock(self.db.config.data/"worker.lock"):
            recover(self.db)
            while not self.stopped:
                if (self.db.config.data/"worker.stop").exists():break
                self.db.put("worker","main",{"status":"IDLE","heartbeat":time.time(),"pid":os.getpid()})
                self.schedule()
                if not self.run_one():time.sleep(1)
            self.db.put("worker","main",{"status":"STOPPED","heartbeat":time.time()})

def main():
    db=Database(Config())
    logs=db.config.data/"logs";logs.mkdir(exist_ok=True)
    handler=RotatingFileHandler(logs/"worker.log",maxBytes=2_000_000,backupCount=3,encoding="utf-8")
    logging.basicConfig(level=logging.INFO,handlers=[handler],format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("telethon").setLevel(logging.ERROR)
    worker=Worker(db)
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:setattr(worker,"stopped",True))
    try:worker.run()
    finally:db.engine.dispose()

if __name__=="__main__":main()
