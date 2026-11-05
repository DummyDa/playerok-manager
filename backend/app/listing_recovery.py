"""Dismiss a stalled publication without replaying or erasing remote writes."""
import time
from sqlalchemy import select,update
from .db import Entity,Task


def dismiss(db,oid):
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE");table=Entity.__table__
        rows=dict(conn.execute(select(table.c.id,table.c.data).where(table.c.kind=="operation")).all())
        root=rows.get(oid)
        if not root or root.get("action")!="create" or root.get("payload",{}).get("resume_of"):
            raise ValueError("Незавершённая публикация не найдена")
        if root.get("state") not in {"UNKNOWN","FAILED"}:raise ValueError("Публикация уже завершена или выполняется")
        family={oid}
        while True:
            expanded=family|{key for key,value in rows.items() if value.get("payload",{}).get("resume_of") in family}
            if expanded==family:break
            family=expanded
        if any(rows[key].get("state") in {"SUBMITTED","QUEUED"} for key in family):raise ValueError("Публикация выполняется. Дождитесь завершения запроса")
        tasks=[task for task in conn.execute(select(Task)).all() if task.payload.get("operation_id") in family]
        if any(task.state=="RUNNING" for task in tasks):raise ValueError("Публикация сейчас обрабатывается. Попробуйте после завершения задачи")
        for key in family:
            if rows[key].get("state")=="PREVIEW":
                conn.execute(table.update().where(table.c.kind=="operation",table.c.id==key).values(data={**rows[key],"state":"CANCELLED"},updated=time.time()))
        conn.execute(table.update().where(table.c.kind=="operation",table.c.id==oid).values(data={**root,"recovery_dismissed":True,"dismissed_at":root.get("dismissed_at") or time.time()},updated=time.time()))
        pending=[t.id for t in tasks if t.state=="READY" and t.kind in {"preview_resume_create","execute"}]
        if pending:conn.execute(update(Task).where(Task.id.in_(pending),Task.state=="READY").values(state="CANCELLED",dedupe=None,lease=None))
        conn.commit()
    return {"id":oid,"dismissed":True}
