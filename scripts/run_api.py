from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend.app.config import ROOT
if __name__=="__main__":
    folder=ROOT/"data/logs";folder.mkdir(parents=True,exist_ok=True)
    log_config={"version":1,"disable_existing_loggers":False,"formatters":{"standard":{"format":"%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers":{"file":{"class":"logging.handlers.RotatingFileHandler","filename":str(folder/"api.log"),"maxBytes":2000000,"backupCount":3,"encoding":"utf-8","formatter":"standard"}},
        "loggers":{"uvicorn":{"handlers":["file"],"level":"INFO","propagate":False},"uvicorn.access":{"handlers":["file"],"level":"WARNING","propagate":False}},"root":{"handlers":["file"],"level":"WARNING"}}
    uvicorn.run("backend.app.main:app",host="127.0.0.1",port=8101,log_config=log_config)
