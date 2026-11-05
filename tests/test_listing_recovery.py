from io import BytesIO
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend.app.db import Task
from backend.app.main import create_app
from backend.app.listing_recovery import dismiss
from backend.app.operations import new_operation,submit
from backend.app.integrations.listing_images import optimize


def pending(db):
    op=new_operation(db,"create",{"fields":{"title":"Gift"},"image":"image"},{"cost_rub":"25"})
    return db.put("operation",op["id"],{"state":"UNKNOWN","submitted_at":1,"error":"upload timeout"})


def test_dismiss_keeps_unknown_result_and_cancels_unused_resume(db):
    root=pending(db)
    preview=new_operation(db,"create",{"resume_of":root["id"],"fields":{"title":"Gift"}})
    task=db.enqueue("preview_resume_create",{"operation_id":root["id"]})
    dismiss(db,root["id"]);dismiss(db,root["id"])
    saved=db.get("operation",root["id"])
    assert saved["state"]=="UNKNOWN" and saved["submitted_at"]==1 and saved["payload"]==root["payload"]
    assert saved["expected"]==root["expected"] and saved["recovery_dismissed"]
    assert db.get("operation",preview["id"])["state"]=="CANCELLED"
    assert submit(db,preview["id"])["state"]=="CANCELLED"
    with db.sessions() as s:assert s.get(Task,task).state=="CANCELLED"


@pytest.mark.parametrize("state",["QUEUED","SUBMITTED"])
def test_dismiss_rejects_running_remote_publication(db,state):
    root=pending(db)
    child=new_operation(db,"create",{"resume_of":root["id"]})
    db.put("operation",child["id"],{"state":state})
    with pytest.raises(ValueError,match="выполняется"):dismiss(db,root["id"])
    assert not db.get("operation",root["id"]).get("recovery_dismissed")


def test_dismiss_checks_running_worker_task(db):
    root=pending(db);tid=db.enqueue("preview_resume_create",{"operation_id":root["id"]})
    with db.tx() as s:s.get(Task,tid).state="RUNNING"
    with pytest.raises(ValueError,match="обрабатывается"):dismiss(db,root["id"])


def test_recovery_removal_requires_owner_and_disappears_from_list(db):
    root=pending(db)
    with TestClient(create_app(db)) as client:
        url='/api/v1/lots/recoveries/'+root['id']
        assert client.delete(url).status_code==401
        auth=client.post('/api/v1/auth/setup',json={'password':'long-test-password'})
        assert client.delete(url).status_code==403
        assert len(client.get('/api/v1/lots/recoveries').json()['items'])==1
        assert client.delete(url,headers={'X-CSRF-Token':auth.json()['csrf']}).status_code==200
        assert client.get('/api/v1/lots/recoveries').json()['items']==[]


def test_opaque_cover_is_compressed_before_upload():
    image=Image.effect_noise((1448,1086),70).convert('RGB');out=BytesIO();image.save(out,'PNG')
    original=out.getvalue();assert len(original)>512*1024
    data,name=optimize(original,'cover.png')
    assert name=='listing.jpg' and len(data)<len(original)
    assert Image.open(BytesIO(data)).size==(1448,1086)


def test_cover_transparency_is_preserved():
    image=Image.effect_noise((1000,1000),70).convert('RGBA');image.putalpha(128)
    out=BytesIO();image.save(out,'PNG');data,name=optimize(out.getvalue(),'cover.png')
    decoded=Image.open(BytesIO(data))
    assert decoded.getchannel('A').getextrema()==(128,128)
