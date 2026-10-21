from pathlib import Path
import os,subprocess,sys,shutil
if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parents[1]
def run(args,cwd=ROOT):subprocess.run(args,cwd=cwd,check=True,env={**os.environ,"NEXT_TELEMETRY_DISABLED":"1","PYTHONUTF8":"1"})
if __name__=="__main__":
    python=ROOT/".venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")
    if not python.exists():run([sys.executable,"-m","venv",str(ROOT/".venv")])
    run([str(python),"-m","pip","install","-r","requirements.txt","--disable-pip-version-check"])
    run([str(python),"scripts/migrate.py"])
    run([str(python),"scripts/build_guide.py"])
    npm=shutil.which("npm.cmd" if os.name=="nt" else "npm")
    if not npm:raise SystemExit("Установите Node.js 22.9+ и повторите setup.bat")
    run([npm,"ci" if (ROOT/"frontend/package-lock.json").exists() else "install","--no-audit","--no-fund"],ROOT/"frontend")
    run([str(python),"scripts/export_openapi.py"])
    run([npm,"run","generate:api"],ROOT/"frontend")
    run([npm,"run","build"],ROOT/"frontend")
    print("Готово. Запустите run.bat. Автоматические торговые действия по умолчанию выключены.")
