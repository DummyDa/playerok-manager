from __future__ import annotations
import argparse
import json
import os
import socket
import re
import subprocess
import sys
if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
import time
import webbrowser
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import ROOT
from backend.app.process_lock import process_lock as worker_lock

DATA=ROOT/"data"
STATE=DATA/"processes.json"

def available(port):
    with socket.socket() as s:return s.connect_ex(("127.0.0.1",port))!=0

def state():
    try:return json.loads(STATE.read_text())
    except Exception:return {}

def launch_pending_update():
    """Spawn as a sibling of API, so Windows taskkill /T cannot kill the updater."""
    request=DATA/"updates/request.json"
    if not request.exists():return False
    claimed=request.with_name("dispatched.json")
    request.replace(claimed)
    try:
        data=json.loads(claimed.read_text(encoding="utf-8"))
        if not all(re.fullmatch(r"[0-9a-f]{40}",data.get(k,"")) for k in ("target","previous")):raise ValueError("Invalid update request")
        if not 0<=time.time()-data.get("created_at",0)<=600:raise ValueError("Update request expired")
        runner=DATA/"updates/runner.py"
        if not runner.is_file():raise ValueError("Missing installer")
        with (DATA/"updates/install.log").open("ab") as output:
            flags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=="nt" else 0
            subprocess.Popen([sys.executable,"-u",str(runner),"--root",str(ROOT),"--target",data["target"],"--previous",data["previous"]],
                             cwd=ROOT,stdout=output,stderr=output,creationflags=flags,start_new_session=os.name!="nt")
        return True
    except Exception:
        from backend.app.updates import write_progress
        from backend.app.db import Database
        from backend.app.config import Config
        write_progress(Database(Config(ROOT)),{"state":"FAILED","message":"Не удалось запустить установщик. Перезапустите приложение и повторите проверку."})
        return False

def supervise():
    DATA.mkdir(exist_ok=True)
    with worker_lock(DATA/"launcher.lock"):
        for name in ["stop.request","worker.stop"]:(DATA/name).unlink(missing_ok=True)
        if any(not available(port) for port in (8101,3100,3180)):raise SystemExit("Порт 3100, 8101 или 3180 уже занят; остановите отдельный каталог")
        node=__import__("shutil").which("node")
        if not node:raise SystemExit("Node.js не найден")
        env={**os.environ,"NEXT_TELEMETRY_DISABLED":"1","PYTHONUTF8":"1"}
        commands={"api":[sys.executable,str(ROOT/"scripts/run_api.py")],"worker":[sys.executable,"-m","backend.app.worker"],
                  "frontend":[node,str(ROOT/"frontend/node_modules/next/dist/bin/next"),"start","--hostname","127.0.0.1","--port","3100"],
                  "catalog":[sys.executable,str(ROOT/"catalog/server.py")]}
        processes={};handles=[];restarts={name:0 for name in commands}
        flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
        logs=DATA/"logs";logs.mkdir(exist_ok=True)
        def spawn(name):
            log=logs/(name+"-console.log")
            if log.exists() and log.stat().st_size>2_000_000:
                log.replace(log.with_suffix(".previous.log"))
            out=log.open("ab");handles.append(out)
            return subprocess.Popen(commands[name],cwd=ROOT/"frontend" if name=="frontend" else ROOT,env=env,stdout=out,stderr=out,creationflags=flags)
        try:
            subprocess.run([sys.executable,str(ROOT/"scripts/migrate.py")],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,creationflags=flags)
            processes["api"]=spawn("api")
            import requests
            ready=False
            for _ in range(30):
                if processes["api"].poll() is not None:break
                try:
                    ready=requests.get("http://127.0.0.1:8101/api/v1/health",timeout=1).status_code==200
                except requests.RequestException:pass
                if ready:break
                time.sleep(1)
            if not ready:raise RuntimeError("API не запустился; проверьте data/logs/api-console.log")
            for name in ["frontend","worker","catalog"]:processes[name]=spawn(name)
            while not (DATA/"stop.request").exists():
                STATE.write_text(json.dumps({"supervisor":os.getpid(),"heartbeat":time.time(),"services":{k:{"pid":p.pid,"running":p.poll() is None} for k,p in processes.items()}}))
                launch_pending_update()
                for name,p in list(processes.items()):
                    if p.poll() is not None:
                        restarts[name]+=1
                        if restarts[name]>3:
                            raise RuntimeError(f"Служба {name} завершилась несколько раз; проверьте data/logs")
                        time.sleep(2);processes[name]=spawn(name)
                time.sleep(1)
        finally:
            (DATA/"worker.stop").write_text("stop")
            worker=processes.get("worker")
            if worker:
                try:worker.wait(timeout=45)
                except subprocess.TimeoutExpired:worker.terminate()
            for p in processes.values():
                if p.poll() is None:
                    if os.name=="nt":subprocess.run(["taskkill","/PID",str(p.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
                    else:p.terminate()
            for p in processes.values():
                try:p.wait(timeout=10)
                except subprocess.TimeoutExpired:p.kill()
            for h in handles:h.close()
            STATE.write_text(json.dumps({"stopped":True,"heartbeat":time.time()}))

def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["start","stop","status","supervise"]);parser.add_argument("--no-browser",action="store_true")
    args=parser.parse_args();DATA.mkdir(exist_ok=True)
    if args.action=="supervise":return supervise()
    s=state();alive=bool(s.get("heartbeat") and time.time()-s["heartbeat"]<10 and not s.get("stopped"))
    if args.action=="status":print(json.dumps({"running":alive,**s},indent=2));return
    if args.action=="stop":
        (DATA/"stop.request").write_text("stop")
        print("Запрошена остановка. Воркер завершит текущую операцию.");return
    if alive:
        print("Playerok Manager уже запущен: http://localhost:3100")
        if not args.no_browser:webbrowser.open("http://localhost:3100")
        return
    if any(not available(port) for port in (8101,3100,3180)):raise SystemExit("Порт 3100, 8101 или 3180 занят другим процессом. Остановите отдельный каталог.")
    if not (ROOT/"frontend/.next/BUILD_ID").exists():raise SystemExit("Сначала запустите setup.bat")
    log=(DATA/"launcher.log").open("ab")
    flags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=="nt" else 0
    subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"supervise"],cwd=ROOT,stdout=log,stderr=log,creationflags=flags,start_new_session=os.name!="nt")
    log.close()
    import requests
    ready=False
    for _ in range(30):
        try:ready=requests.get("http://127.0.0.1:3100",timeout=1).status_code==200 and requests.get("http://127.0.0.1:3180/api/health",timeout=1).status_code==200
        except requests.RequestException:pass
        if ready:break
        time.sleep(1)
    if not ready:raise SystemExit("Службы ещё не готовы. Проверьте status.bat и data/logs.")
    print("Playerok Manager: http://localhost:3100 · Каталог: http://localhost:3180")
    if not args.no_browser:webbrowser.open("http://localhost:3100")

if __name__=="__main__":main()
