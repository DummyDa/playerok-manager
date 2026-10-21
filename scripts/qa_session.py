"""Temporary local QA session; never creates or changes the administrator password."""
import sys,json,hashlib,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.config import Config
from backend.app.db import Database,Login
from backend.app.auth import create_session
if __name__=="__main__":
    db=Database(Config());path=db.config.data/"qa/session.json"
    if len(sys.argv)>1 and sys.argv[1]=="cleanup":
        if path.exists():
            digest=hashlib.sha256(json.loads(path.read_text())["token"].encode()).hexdigest()
            with db.tx() as s:
                row=s.get(Login,digest)
                if row:s.delete(row)
            path.unlink()
        print("Temporary QA session removed")
    else:
        token,csrf=create_session(db)
        # Restrict the temporary session to ten minutes, even if QA is interrupted.
        with db.tx() as s:s.get(Login,hashlib.sha256(token.encode()).hexdigest()).expires=time.time()+600
        path.parent.mkdir(exist_ok=True);path.write_text(json.dumps({"token":token}))
        print("Temporary local QA session prepared; token not printed")
