import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.main import app
from backend.app.config import ROOT
if __name__=="__main__":
    (ROOT/"data").mkdir(exist_ok=True)
    (ROOT/"data/openapi.json").write_text(json.dumps(app.openapi(),ensure_ascii=False),encoding="utf-8")
