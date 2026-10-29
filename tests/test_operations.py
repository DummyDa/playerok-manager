from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import time
import pytest
from sqlalchemy import select
from backend.app.operations import new_operation,submit,recover
from backend.app.db import Task
from backend.app.service import Service
from backend.app.worker import Worker,worker_lock
from backend.app.integrations.clients import WriteBlocked,GuardSession,Mrkt,gate

def test_emergency_stop_enforced_at_service_and_transport(db):
    db.configure(emergency_stop=True)
    op=new_operation(db,"update",{"lot_id":"l","changes":{"price":1}})
    with pytest.raises(WriteBlocked):submit(db,op["id"])
    with pytest.raises(WriteBlocked):GuardSession(db).post("https://playerok.com/graphql",json={})
    with pytest.raises(WriteBlocked):Mrkt(db).buy({"id":"gift","salePrice":1})

def test_concurrent_confirmation_creates_one_task(db):
    op=new_operation(db,"update",{"lot_id":"l","changes":{"price":1}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:submit(db,op["id"]),range(2)))
    assert all(r["state"]=="QUEUED" for r in results)
    with db.sessions() as s:assert len(list(s.scalars(select(Task))))==1

def test_recovery_never_resends_submitted_money(db):
    db.put("operation","buy",{"state":"SUBMITTED","action":"order.buy","payload":{},"order_id":"deal"})
    db.put("order","deal",{"stage":"PURCHASING"})
    recover(db)
    assert db.get("operation","buy")["state"]=="UNKNOWN"
    assert db.get("order","deal")["stage"]=="MANUAL"
    Service(db).execute("buy")
    assert db.get("operation","buy")["state"]=="UNKNOWN"

def test_restart_recovers_queued_intent(db):
    db.put("operation","op",{"state":"QUEUED","action":"promotion","payload":{}})
    recover(db);recover(db)
    with db.sessions() as s:assert len(list(s.scalars(select(Task))))==1

def test_queue_claim_and_dedupe(db):
    db.enqueue("fx");db.enqueue("fx")
    worker=Worker(db)
    claimed=worker.claim()
    assert claimed[1]=="fx"
    assert worker.claim() is None

def test_estimate_state_replacement_clears_old_errors_and_prices(db,monkeypatch):
    from backend.app.pricing import PricingError
    db.put("lot","l",{"pair_id":"p","status":"APPROVED"})
    svc=Service(db)
    db.put("estimate","l",{"status":"READY","sale_price":100,"profit_rub":"10"})
    def blocked(*args):raise PricingError("Настройте комиссии")
    monkeypatch.setattr(svc,"estimate",blocked)
    svc.reprice()
    failed=db.get("estimate","l")
    assert failed["status"]=="BLOCKED" and "sale_price" not in failed
    result={"sale_price":200,"profit_rub":"20","strategy":{"enabled":False}}
    monkeypatch.setattr(svc,"estimate",lambda *args:result)
    svc.reprice()
    assert db.get("estimate","l")["status"]=="READY"
    assert "error" not in db.get("estimate","l")
    db.put("estimate","l",{"error":"Старая ошибка"})
    svc.preview_price("l")
    assert "error" not in db.get("estimate","l")

def test_worker_lock_prevents_second_instance(tmp_path):
    with worker_lock(tmp_path/"worker.lock"):
        with pytest.raises(RuntimeError):
            with worker_lock(tmp_path/"worker.lock"):pass

def test_ledger_is_idempotent_and_immutable(db):
    svc=Service(db);svc.ledger("op","purchase",100,"l");svc.ledger("op","purchase",999,"l")
    assert len(db.list("ledger"))==1
    assert db.list("ledger")[0]["amount_rub"]=="100"

def test_emergency_stop_blocks_execution(db):
    db.configure(emergency_stop=True)
    with pytest.raises(WriteBlocked):gate(db)

def test_monetary_timeout_is_unknown_not_retried(db,monkeypatch):
    db.configure(promotions_enabled=True,daily_promotion_limit_rub="100")
    db.put("lot","lot",{"status":"APPROVED","price":100,"slug":"slug"})
    op=new_operation(db,"promotion",{"lot_id":"lot"},{"status_id":"premium","cost_rub":"9","lot_price":100})
    submit(db,op["id"])
    from backend.app import service
    fake=Mock();fake.premium.return_value={"id":"premium","price":9};fake.viewer.return_value={"balance":{"available":100}}
    fake.promote.side_effect=TimeoutError("uncertain")
    monkeypatch.setattr(service,"Playerok",lambda _:fake)
    svc=Service(db);monkeypatch.setattr(svc,"refresh_lot",lambda _:None)
    svc.execute(op["id"]);svc.execute(op["id"])
    assert fake.promote.call_count==1
    assert db.get("operation",op["id"])["state"]=="UNKNOWN"

def test_tariff_change_aborts_without_payment(db,monkeypatch):
    db.configure(promotions_enabled=True,daily_promotion_limit_rub="100")
    db.put("lot","l",{"status":"APPROVED","price":100})
    op=new_operation(db,"promotion",{"lot_id":"l"},{"status_id":"old","cost_rub":"9","lot_price":100});submit(db,op["id"])
    from backend.app import service
    fake=Mock();fake.premium.return_value={"id":"new","price":25}
    monkeypatch.setattr(service,"Playerok",lambda _:fake)
    svc=Service(db);monkeypatch.setattr(svc,"refresh_lot",lambda _:None);svc.execute(op["id"])
    fake.promote.assert_not_called()
    assert db.get("operation",op["id"])["state"]=="FAILED"

def test_daily_premium_budget_blocks(db,monkeypatch):
    db.configure(promotions_enabled=True,daily_promotion_limit_rub="10")
    db.put("lot","l",{"status":"APPROVED","price":100})
    svc=Service(db);svc.ledger("previous","promotion",9,"l")
    op=new_operation(db,"promotion",{"lot_id":"l"},{"status_id":"p","cost_rub":"9","lot_price":100});submit(db,op["id"])
    from backend.app import service
    fake=Mock();fake.premium.return_value={"id":"p","price":9}
    monkeypatch.setattr(service,"Playerok",lambda _:fake);monkeypatch.setattr(svc,"refresh_lot",lambda _:None)
    svc.execute(op["id"]);fake.promote.assert_not_called()


@pytest.mark.parametrize("daily,per_purchase,allowed",[("0","0",True),("0","10",True),("0","5",False),("10","0",False)])
def test_zero_premium_limits_are_unlimited(db,monkeypatch,daily,per_purchase,allowed):
    db.configure(promotions_enabled=True,daily_promotion_limit_rub=daily)
    db.put("lot","l",{"status":"APPROVED","price":100})
    db.put("promotion_schedule","l",{"enabled":True,"max_cost_rub":per_purchase})
    svc=Service(db);svc.ledger("previous","promotion",9,"l")
    op=new_operation(db,"promotion",{"lot_id":"l","automatic":True},{"status_id":"p","cost_rub":"9","lot_price":100})
    submit(db,op["id"])
    fake=Mock();fake.premium.return_value={"id":"p","price":9};fake.viewer.return_value={"balance":{"available":100}}
    fake.promote.return_value={"id":"l","name":"Lot","price":100,"priority":"PREMIUM","priorityPrice":9}
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:fake)
    monkeypatch.setattr(svc,"refresh_lot",lambda _:None)
    svc.execute(op["id"])
    if allowed:
        fake.promote.assert_called_once()
        assert db.get("operation",op["id"])["state"]=="COMPLETE"
    else:
        fake.promote.assert_not_called()
        assert db.get("operation",op["id"])["state"]=="FAILED"

def test_backup_retains_seven_consistent_databases(db):
    folder=db.config.data/"backups";folder.mkdir()
    for i in range(9):(folder/f"2020-01-{i+1:02d}.sqlite3").touch()
    Service(db).backup()
    assert len(list(folder.glob("*.sqlite3")))==7
    import sqlite3
    latest=sorted(folder.glob("*.sqlite3"))[-1]
    with sqlite3.connect(latest) as c:assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"

def test_unknown_charge_reserves_budget_until_manual_resolution(db):
    db.put("operation","uncertain",{"action":"promotion","state":"UNKNOWN","expected":{"cost_rub":"25"},"payload":{"lot_id":"l"}})
    service=Service(db)
    assert service.daily_spent("promotion")==25
    service.resolve_operation("uncertain","NOT_EXECUTED","Проверена история: списания не было")
    assert service.daily_spent("promotion")==0
    assert db.get("operation","uncertain")["state"]=="NOT_EXECUTED"

def test_confirmed_unknown_charge_is_counted_once(db):
    db.put("operation","uncertain",{"action":"promotion","state":"UNKNOWN","expected":{"cost_rub":"25"},"payload":{"lot_id":"l"}})
    service=Service(db)
    service.resolve_operation("uncertain","CONFIRMED","Списание подтверждено в истории Playerok")
    assert service.daily_spent("promotion")==25
    assert len(db.list("ledger"))==1
