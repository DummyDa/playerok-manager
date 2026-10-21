import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alembic import command
from alembic.config import Config as AlembicConfig
from backend.app.config import ROOT,Config
from backend.app.db import Database
if __name__ == "__main__":
    cfg=AlembicConfig(str(ROOT/"alembic.ini"));cfg.set_main_option("script_location",str(ROOT/"backend/migrations"))
    command.upgrade(cfg,"head")
    Database(Config()).initialize()
