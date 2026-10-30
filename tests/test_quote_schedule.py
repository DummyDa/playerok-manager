import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from backend.app.db import Task
from backend.app.integrations.clients import Mrkt
from backend.app.safety import IntegrationError
from backend.app.service import Service
from backend.app.worker import Worker


def tasks(db):
    with db.sessions() as session:
        return list(session.scalars(select(Task).where(Task.kind=="quote").order_by(Task.due)))


def test_all_enabled_pairs_checked_oldest_first_without_active_listings(db):
    now=time.time()
    for key in ["old","newer","fresh","disabled","unavailable"]:
        db.put("pair",key,{"collection":"Gift","model":key,"enabled":key!="disabled"})
    db.put("quote","old",{"fetched_at":now-100000})
    db.put("quote","newer",{"fetched_at":now-4000})
    db.put("quote","fresh",{"fetched_at":now})
    db.put("quote","unavailable",{"available":False,"fetched_at":now})
    service=Service(db);service.quotes();service.quotes()
    queued=tasks(db)
    assert [t.payload["pair_id"] for t in queued]==["old","newer"]
    assert queued[1].due-queued[0].due==3
    assert all(t.payload["scheduled"] for t in queued)


def test_error_backoff_respected_and_shorter_interval_applies_immediately(db):
    db.put("pair","p",{"collection":"Gift","model":"Model"})
    db.put("quote","p",{"fetched_at":time.time()-10000,"last_error":"429","next_check_at":time.time()+600})
    Service(db).quotes();assert tasks(db)==[]
    db.put("quote","p",{"fetched_at":time.time()-100,"last_error":None,"next_check_at":time.time()+3500})
    db.configure(quote_interval=60)
    Service(db).quotes();assert len(tasks(db))==1


def test_worker_waits_for_global_cooldown_without_blocking_other_tasks(db):
    db.enqueue("quote",{"pair_id":"p"},key="quote:p")
    db.put("throttle","mrkt",{"next":time.time()+600})
    db.enqueue("fx")
    worker=Worker(db)
    assert worker.claim()[1]=="fx"
    assert worker.claim() is None
    assert tasks(db)[0].due>=db.get("throttle","mrkt")["next"]


def test_catchup_queue_is_paced_after_long_cooldown(db):
    db.enqueue("quote",{"pair_id":"a"},key="quote:a")
    db.enqueue("quote",{"pair_id":"b"},key="quote:b")
    worker=Worker(db)
    assert worker.claim()[1]=="quote"
    assert worker.claim() is None


def test_pair_failure_preserves_success_date_and_does_not_lose_other_pairs(db,monkeypatch):
    for key in ["a","b"]:db.put("pair",key,{"collection":"Gift","model":key})
    db.put("quote","a",{"available":True,"fetched_at":123,"ton":"1"})
    worker=Worker(db)
    monkeypatch.setattr(worker.service.mrkt,"search",Mock(side_effect=IntegrationError("MRKT limited")))
    worker.service.quotes()
    assert worker.run_one()
    saved=db.get("quote","b") or db.get("quote","a")
    assert saved["last_error"] and saved["next_check_at"]>time.time()
    assert db.get("quote","a")["fetched_at"]==123
    assert len(tasks(db))==2 and all(t.state=="READY" for t in tasks(db))
    failed=next(t for t in tasks(db) if t.error)
    assert failed.due>=db.get("quote",failed.payload["pair_id"])["next_check_at"]


def test_success_clears_error_and_no_offers_are_checked_hourly_too(db,monkeypatch):
    db.put("pair","p",{"collection":"Gift","model":"Model"})
    db.put("quote","p",{"fetched_at":1,"last_error":"failed","failures":3})
    service=Service(db);monkeypatch.setattr(service.mrkt,"search",lambda *_:[])
    service.refresh_quote("p")
    saved=db.get("quote","p")
    assert saved["last_error"] is None and saved["failures"]==0 and not saved["available"]
    assert 3599<saved["next_check_at"]-time.time()<=3600
    service.quotes();assert tasks(db)==[]


def test_mrkt_cooldown_does_not_attempt_telegram_authorization(db,monkeypatch):
    client=Mrkt(db);authenticate=Mock();monkeypatch.setattr(client,"authenticate",authenticate)
    db.put("throttle","mrkt",{"next":time.time()+60})
    with pytest.raises(IntegrationError,match="пауза"):client.request("POST","/gifts/saling",{})
    authenticate.assert_not_called()


@pytest.mark.parametrize("header",["120","NaN","invalid"])
def test_mrkt_429_persists_cooldown_and_increases_backoff(db,monkeypatch,header):
    client=Mrkt(db);client.token="test"
    monkeypatch.setattr(client.http,"request",Mock(return_value=NS(status_code=429,headers={"Retry-After":header})))
    monkeypatch.setattr("backend.app.integrations.clients.time.sleep",lambda *_:None)
    for attempt in range(1,4):
        db.put("throttle","mrkt",{"next":0})
        with pytest.raises(IntegrationError,match="частоту"):client.request("POST","/gifts/saling",{})
        throttle=db.get("throttle","mrkt")
        assert throttle["rate_limits"]==attempt
        assert throttle["next"]-time.time()>=60*2**(attempt-1)-1


def test_old_hourly_scheduler_clock_is_migrated_to_pair_polling(db,monkeypatch):
    monkeypatch.setattr("backend.app.runtime_config.has_user_session",lambda _:True)
    db.put("clock","quotes",{"next":time.time()+3500})
    Worker(db).schedule()
    with db.sessions() as session:
        assert session.scalar(select(Task).where(Task.kind=="quotes")) is not None
    assert db.get("clock","quotes")["next"]-time.time()<=30
