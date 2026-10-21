from alembic import context
from backend.app.config import Config
from backend.app.db import Database, Base

db = Database(Config())
with db.engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction(): context.run_migrations()
