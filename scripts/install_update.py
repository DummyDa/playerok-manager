"""Detached, opt-in updater. Only a clean, exact upstream fast-forward is accepted.

Copied into data/updates before launch, so source replacement cannot replace this runner.
No credentials are read or sent to GitHub. Diagnostics stay in data/updates/install.log.
"""
import argparse
from contextlib import contextmanager,closing
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

REMOTE="https://github.com/DummyDa/playerok-manager.git"

@contextmanager
def lock(path):
    with path.open("a+b") as handle:
        handle.write(b"0");handle.flush();handle.seek(0)
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=="nt":handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)

def progress(root,state,message,**extra):
    path=root/"data/updates/status.json";tmp=path.with_suffix(".runner.tmp")
    old=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    tmp.write_text(json.dumps({**old,"state":state,"message":message,"updated_at":time.time(),**extra}),encoding="utf-8");tmp.replace(path)

def run(root,args,timeout=120,capture=False):
    result=subprocess.run(args,cwd=root,capture_output=capture,text=True,timeout=timeout,
                          env={**os.environ,"GIT_TERMINAL_PROMPT":"0","GCM_INTERACTIVE":"never","PYTHONUTF8":"1","NEXT_TELEMETRY_DISABLED":"1"},
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    if result.returncode:raise RuntimeError("Command failed: "+Path(str(args[0])).name)
    return result.stdout.strip() if capture else None

def verify_checkout(root,target,previous):
    git=shutil.which("git")
    if not git:raise RuntimeError("Git unavailable")
    if Path(run(root,[git,"rev-parse","--show-toplevel"],capture=True)).resolve()!=root:raise RuntimeError("Unexpected repository root")
    if run(root,[git,"rev-parse","HEAD"],capture=True)!=previous:raise RuntimeError("Installed version changed")
    if run(root,[git,"status","--porcelain"],capture=True):raise RuntimeError("Local source changes detected")
    run(root,[git,"fetch","--no-tags",REMOTE,"main"])
    if run(root,[git,"rev-parse","FETCH_HEAD"],capture=True)!=target:raise RuntimeError("Upstream changed since confirmation")
    run(root,[git,"merge-base","--is-ancestor",previous,target])
    return git

def stopped(root):
    try:
        state=json.loads((root/"data/processes.json").read_text())
        if not state.get("stopped"):return False
    except (OSError,ValueError):return False
    for port in (3100,8101):
        with socket.socket() as s:
            s.settimeout(.3)
            if s.connect_ex(("127.0.0.1",port))==0:return False
    return True

def stop(root,python):
    run(root,[python,"scripts/launcher.py","stop"])
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        if stopped(root):return
        time.sleep(1)
    raise RuntimeError("Services did not stop; source and data must remain intact")

def backup_data(root,folder):
    source=root/"data";destination=folder/"data";destination.mkdir(parents=True,exist_ok=False)
    excluded={"updates","logs","qa","release","backups","manager.sqlite3","manager.sqlite3-wal","manager.sqlite3-shm","processes.json","launcher.lock","worker.lock","stop.request","worker.stop"}
    for item in source.iterdir():
        if item.name in excluded:continue
        if item.is_symlink():raise RuntimeError("Symbolic links in data require manual update")
        if item.is_dir():shutil.copytree(item,destination/item.name)
        else:shutil.copy2(item,destination/item.name)
    with closing(sqlite3.connect(source/"manager.sqlite3")) as old,closing(sqlite3.connect(destination/"manager.sqlite3")) as new:
        old.backup(new)
    if (root/".env").exists():shutil.copy2(root/".env",folder/"legacy.env.bak")

def safe_mode(root):
    with closing(sqlite3.connect(root/"data/manager.sqlite3")) as db:
        row=db.execute("SELECT data FROM entities WHERE kind='settings' AND id='default'").fetchone()
        if not row:raise RuntimeError("Settings missing")
        data=json.loads(row[0]);data.update(emergency_stop=True)
        if "dry_run" in data:data["dry_run"]=True  # Compatibility when restoring an older version.
        db.execute("UPDATE entities SET data=?,updated=? WHERE kind='settings' AND id='default'",(json.dumps(data),time.time()))
        db.commit()

def restore_data(root,folder):
    source=folder/"data";destination=root/"data"
    # All destinations are fixed children of the validated checkout. Services must be stopped.
    for suffix in ("-wal","-shm"):(destination/("manager.sqlite3"+suffix)).unlink(missing_ok=True)
    for item in source.iterdir():
        if item.is_dir():shutil.copytree(item,destination/item.name,dirs_exist_ok=True)
        else:shutil.copy2(item,destination/item.name)
    safe_mode(root)

def install(root,target,previous):
    root=root.resolve();updates=root/"data/updates";updates.mkdir(parents=True,exist_ok=True)
    python=str(root/".venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python"))
    changed=False;services_stopped=False;folder=None;git=None
    with lock(updates/"install.lock"):
        try:
            progress(root,"FETCHING","Проверяем подтверждённый коммит GitHub")
            git=verify_checkout(root,target,previous)
            progress(root,"STOPPING","Завершаем работу служб")
            stop(root,python);services_stopped=True
            if run(root,[git,"status","--porcelain"],capture=True):raise RuntimeError("Source changed while stopping")
            progress(root,"BACKUP","Сохраняем базу, настройки, ключ и сессии")
            folder=updates/("backup-"+str(time.time_ns()));backup_data(root,folder)
            (folder/"version.json").write_text(json.dumps({"previous":previous,"target":target}),encoding="utf-8")
            safe_mode(root)
            progress(root,"INSTALLING","Устанавливаем зависимости и собираем новую версию. Это может занять несколько минут.",backup=folder.name)
            run(root,[git,"merge","--ff-only",target]);changed=True
            run(root,[python,"scripts/setup.py"],timeout=1800)
            safe_mode(root)
            progress(root,"RESTARTING","Запускаем новую версию")
            run(root,[python,"scripts/launcher.py","start","--no-browser"],timeout=90)
            progress(root,"COMPLETE","Обновление установлено. Автоматизация приостановлена; проверьте настройки перед продолжением торговли.",installed_commit=target)
        except Exception:
            # Detailed tool output stays in the local log, not in the dashboard.
            import traceback
            traceback.print_exc()
            if changed:
                progress(root,"ROLLING_BACK","Установка не завершена. Восстанавливаем предыдущие исходники и данные.")
                try:
                    stop(root,python)
                    run(root,[git,"reset","--hard",previous])
                    restore_data(root,folder)
                    run(root,[python,"scripts/setup.py"],timeout=1800)
                    safe_mode(root)
                    run(root,[python,"scripts/launcher.py","start","--no-browser"],timeout=90)
                    progress(root,"FAILED","Обновление не установлено. Предыдущая версия восстановлена с приостановленной автоматизацией. Подробности: data/updates/install.log.")
                except Exception:
                    traceback.print_exc();progress(root,"FAILED","Автовосстановление не завершено. Не запускайте торговлю. Копия сохранена в data/updates; инструкция: guide.html, журнал: data/updates/install.log.")
            else:
                if services_stopped:
                    try:run(root,[python,"scripts/launcher.py","start","--no-browser"],timeout=90)
                    except Exception:pass
                progress(root,"FAILED","Установка остановлена до замены исходников. Проверьте локальные изменения и соединение с GitHub. Журнал: data/updates/install.log.")

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--root",required=True,type=Path);parser.add_argument("--target",required=True);parser.add_argument("--previous",required=True)
    args=parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-f]{40}",v) for v in (args.target,args.previous)):raise SystemExit("Invalid commit")
    install(args.root,args.target,args.previous)
