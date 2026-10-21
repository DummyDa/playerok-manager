"""Opt-in updates from the fixed upstream repository. GET never contacts GitHub."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import time
import requests
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select,delete,insert
from .db import Entity
from .schemas import Strict

REPOSITORY="DummyDa/playerok-manager"
REMOTE="https://github.com/"+REPOSITORY+".git"
BRANCH="main"
ACTIVE={"QUEUED","FETCHING","STOPPING","BACKUP","INSTALLING","RESTARTING","ROLLING_BACK"}

def git(root,*args):
    executable=shutil.which("git")
    if not executable:raise ValueError("Для установки обновлений установите Git и перезапустите приложение")
    result=subprocess.run([executable,"-C",str(root),*args],capture_output=True,text=True,timeout=15,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    if result.returncode:raise ValueError("Автообновление доступно для установки через git clone. ZIP-копию обновите вручную по инструкции.")
    return result.stdout.strip()

def installation(root):
    try:
        if Path(git(root,"rev-parse","--show-toplevel")).resolve()!=root.resolve():raise ValueError("Папка проекта должна быть отдельным Git-репозиторием")
        current=git(root,"rev-parse","HEAD")
        dirty=bool(git(root,"status","--porcelain"))
        return {"current_commit":current,"install_supported":not dirty,"install_reason":"Есть локальные изменения исходников. Сохраните их перед обновлением." if dirty else None}
    except (ValueError,subprocess.SubprocessError):
        return {"current_commit":None,"install_supported":False,"install_reason":"Автоустановка требует Git и установку через git clone. Для ZIP-копии используйте ручное обновление из инструкции."}

def read_progress(db):
    try:return json.loads((db.config.data/"updates/status.json").read_text(encoding="utf-8"))
    except (OSError,ValueError):return {"state":"IDLE"}

def write_progress(db,values):
    path=db.config.data/"updates/status.json";path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp");tmp.write_text(json.dumps({**values,"updated_at":time.time()}),encoding="utf-8");tmp.replace(path)

def status(db):
    cached=db.get("update","latest") or {}
    local=installation(db.config.root)
    progress=read_progress(db)
    return {**cached,**local,"repository":REPOSITORY,"branch":BRANCH,"progress":progress,
            "available":bool((local["current_commit"] or cached.get("baseline_commit")) and cached.get("latest_commit") and (local["current_commit"] or cached.get("baseline_commit"))!=cached["latest_commit"]),
            "dismissed":cached.get("dismissed_commit")==cached.get("latest_commit")}

def check(db,http=requests):
    previous=db.get("update","latest") or {}
    try:
        response=http.get(f"https://api.github.com/repos/{REPOSITORY}/commits/{BRANCH}",
                          headers={"Accept":"application/vnd.github+json","User-Agent":"Playerok-Manager-Updater"},timeout=15)
        if response.status_code==403 or response.status_code==429:raise ValueError("GitHub временно ограничил проверки. Повторите позже.")
        response.raise_for_status();data=response.json();sha=data["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}",sha):raise ValueError("GitHub вернул некорректную версию")
        db.put("update","latest",{"latest_commit":sha,"baseline_commit":previous.get("baseline_commit") or sha,"message":str(data["commit"]["message"])[:2000],
                "committed_at":data["commit"]["committer"]["date"],"commit_url":f"https://github.com/{REPOSITORY}/commit/{sha}",
                "checked_at":time.time(),"last_error":None})
    except Exception:
        # Never echo response bodies or environment proxy credentials.
        db.put("update","latest",{"checked_at":time.time(),"last_error":"Не удалось проверить GitHub. Попробуйте позже; работа магазина продолжается."})
    return db.get("update","latest")

def request_install(db,sha):
    from .telegram_login import status as login_status,TERMINAL
    if login_status(db)["state"] not in TERMINAL|{"IDLE"}:raise ValueError("Завершите или отмените вход в Telegram перед обновлением")
    state=status(db)
    if not state["install_supported"]:raise ValueError(state["install_reason"])
    if not state["available"] or sha!=state.get("latest_commit"):raise ValueError("Список обновлений изменился. Проверьте обновления заново.")
    if not state.get("checked_at") or time.time()-state["checked_at"]>3600:raise ValueError("Проверка обновлений устарела. Повторите проверку.")
    try:
        launcher=json.loads((db.config.data/"processes.json").read_text())
        if launcher.get("stopped") or time.time()-launcher.get("heartbeat",0)>10:raise ValueError()
    except (OSError,ValueError):raise ValueError("Для обновления запустите приложение через run.bat")
    source=db.config.root/"scripts/install_update.py"
    if not source.is_file():raise ValueError("Установщик отсутствует. Восстановите файлы приложения.")
    # Serialize repeated clicks across API threads/processes with SQLite's write lock.
    with db.engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        row=connection.execute(select(Entity.data).where(Entity.kind=="update_install",Entity.id=="current")).scalar_one_or_none()
        if row and row.get("state")=="QUEUED" and read_progress(db).get("state") in ACTIVE:
            connection.rollback();return {"started":True,"already_running":True}
        connection.execute(delete(Entity).where(Entity.kind=="update_install",Entity.id=="current"))
        connection.execute(insert(Entity).values(kind="update_install",id="current",data={"state":"QUEUED","target":sha}))
        write_progress(db,{"state":"QUEUED","target":sha,"message":"Обновление готовится. Панель временно перезапустится."})
        connection.commit()
    db.configure(emergency_stop=True)
    try:
        runner=db.config.data/"updates/runner.py";shutil.copyfile(source,runner)
        request=runner.with_name("request.json");temporary=request.with_suffix(".tmp")
        temporary.write_text(json.dumps({"target":sha,"previous":state["current_commit"],"created_at":time.time()}),encoding="utf-8")
        temporary.replace(request)
    except Exception:
        write_progress(db,{"state":"FAILED","message":"Не удалось запустить установщик. Приложение продолжает работу."});raise ValueError("Не удалось запустить установщик")
    return {"started":True}

class UpdateTarget(Strict):commit:str=Field(pattern=r"^[0-9a-f]{40}$")

def router(db):
    routes=APIRouter(prefix="/api/v1/updates")
    @routes.get("")
    def read()->dict:return status(db)
    @routes.post("/check")
    def refresh()->dict:return {"task_id":db.enqueue("check_updates",key="check_updates")}
    @routes.post("/dismiss")
    def dismiss(payload:UpdateTarget)->dict:
        db.put("update","latest",{"dismissed_commit":payload.commit});return {"dismissed":True}
    @routes.post("/install")
    def install(payload:UpdateTarget)->dict:return request_install(db,payload.commit)
    return routes
