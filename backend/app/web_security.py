"""Persistent request budgets, independent of forwarded client identities."""
import hashlib
import time
from sqlalchemy import insert,select,update
from fastapi import HTTPException
from .db import Entity


def budget(db, namespace, identity, limit, seconds):
    key=namespace+":"+hashlib.sha256(identity.encode()).hexdigest()
    now=time.time();table=Entity.__table__
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        old=conn.execute(select(table.c.data).where(table.c.kind=="web_budget",table.c.id==key)).scalar_one_or_none()
        value=old if old and old.get("until",0)>now else {"count":0,"until":now+seconds}
        if value["count"]>=limit:
            conn.rollback()
            raise HTTPException(429,"Слишком много запросов. Попробуйте позже.",headers={"Retry-After":str(max(1,int(value["until"]-now)))})
        value={**value,"count":value["count"]+1}
        if old:conn.execute(update(table).where(table.c.kind=="web_budget",table.c.id==key).values(data=value,updated=now))
        else:conn.execute(insert(table).values(kind="web_budget",id=key,data=value,updated=now))
        # Retain only recent buckets; attacker-controlled addresses cannot grow storage forever.
        conn.execute(table.delete().where(table.c.kind=="web_budget",table.c.updated<now-86400))
        conn.commit()


def local_request(request):
    return bool(request.client and request.client.host in {"127.0.0.1","::1","testclient"}
                and request.url.hostname in {"localhost","127.0.0.1","::1","testserver"})
