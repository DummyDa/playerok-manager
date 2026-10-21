"""Remove the legacy global preview switch without enabling pending automation."""
import json
from alembic import op
from sqlalchemy import text
revision="0002"
down_revision="0001"

def upgrade():
    connection=op.get_bind()
    raw=connection.execute(text("SELECT data FROM entities WHERE kind='settings' AND id='default'")).scalar()
    if not raw:return
    data=json.loads(raw) if isinstance(raw,str) else dict(raw)
    if data.pop("dry_run",False):
        data.update(emergency_stop=True,repricing_enabled=False,purchases_enabled=False,transfers_enabled=False,promotions_enabled=False)
    connection.execute(text("UPDATE entities SET data=:data WHERE kind='settings' AND id='default'"),{"data":json.dumps(data)})

def downgrade():
    # Older code defaults to its safe preview mode when the field is absent.
    pass
