from unittest.mock import Mock
import pytest
from backend.app.integrations.clients import Playerok
from backend.app.integrations.legacy_playerok import PlayerokListingClient,PlayerokListingInput
from backend.app.service import Service
from backend.app.operations import new_operation


def test_resume_listing_uses_existing_draft_without_creating_another(db,monkeypatch):
    from datetime import datetime,timezone
    db.configure(playerok_account_id="seller")
    fields={"title":"Gift","price":500,"description":"text","collection":"Gift","pair_id":"p"}
    original=new_operation(db,"create",{"fields":fields,"image":"cG5n"})
    db.put("operation",original["id"],{"state":"UNKNOWN","submitted_at":datetime.now(timezone.utc).timestamp()})
    draft={"id":"draft","slug":"draft-gift","name":"Gift","price":500,"status":"DRAFT","description":"text","user":{"id":"seller"},"createdAt":datetime.now(timezone.utc).isoformat()}
    remote=Mock();remote.lots.return_value=[draft];remote.item.return_value=draft
    monkeypatch.setattr("backend.app.service.Playerok",lambda db:remote)
    preview=Service(db).preview_resume_create(original["id"])
    assert preview["payload"]["fields"]["resume_item_id"]=="draft"
    assert Service(db).preview_resume_create(original["id"])["id"]==preview["id"]
    transport=PlayerokListingClient.__new__(PlayerokListingClient)
    transport.create_item=Mock(side_effect=AssertionError("must not create duplicate"))
    transport.get_priority_status=Mock(return_value={"id":"free","price":0})
    transport.publish_item=Mock(return_value={"id":"draft","slug":"draft-gift","status":"PENDING_APPROVAL"})
    result=transport.create_and_publish(PlayerokListingInput(title="Gift",collection="Gift",price=500,description="text",image_bytes=b"png"),draft=draft)
    assert result.item_id=="draft"
    transport.create_item.assert_not_called()
    db.put("operation",preview["id"],{"state":"EXPIRED"})
    remote.lots.return_value=[draft,{**draft,"id":"duplicate"}]
    with pytest.raises(ValueError,match="единственный"):Service(db).preview_resume_create(original["id"])


def test_listing_upload_has_longer_read_timeout_and_no_retry(db):
    import requests
    transport=PlayerokListingClient.__new__(PlayerokListingClient)
    transport.config=db.config;transport.session=Mock();transport.graphql_url="https://playerok.com/graphql"
    transport._headers=Mock(return_value={})
    transport.session.post.side_effect=requests.exceptions.ReadTimeout("upload timed out")
    with pytest.raises(Exception,match="createItem"):
        transport._graphql_post_multipart({}, {},"createItem","/sell")
    transport.session.post.assert_called_once()
    assert transport.session.post.call_args.kwargs["timeout"]==(20,120)


@pytest.mark.parametrize("cost,balance,allowed",[(13,100,True),(51,100,False),(13,2,False)])
def test_premium_checked_before_paid_publish(db,cost,balance,allowed):
    db.configure(promotions_enabled=True)
    transport=PlayerokListingClient.__new__(PlayerokListingClient)
    transport.resolve_collection=Mock(return_value="gift")
    transport.create_item=Mock(return_value={"id":"draft","price":500})
    transport.get_priority_status=Mock(return_value={"id":"premium","price":cost})
    transport.get_viewer_has_enabled_notifications=Mock()
    transport.get_viewer_local_balance=Mock()
    transport.publish_item=Mock(return_value={"id":"draft","slug":"gift","status":"PENDING_APPROVAL"})
    client=Playerok.__new__(Playerok);client.db=db;client.client=transport
    client.viewer=Mock(return_value={"balance":{"available":balance}})
    data={"title":"Gift","collection":"Gift","price":500,"premium":True,"premium_max_rub":50}
    if allowed:
        result=client.create(data,b"png")
        assert result["premium"] is True and result["premium_price"]==13
        assert transport.publish_item.call_args.kwargs["premium"] is True
    else:
        with pytest.raises(ValueError):client.create(data,b"png")
        transport.publish_item.assert_not_called()


@pytest.mark.parametrize("limit,success",[(50,True),(10,False)])
def test_create_premium_budget_and_ledger(db,monkeypatch,limit,success):
    db.configure(promotions_enabled=True,daily_promotion_limit_rub=str(limit))
    db.secret("playerok_cookies","test-cookie")
    fields={"title":"Gift","pair_id":"p","price":500,"premium":True,"premium_max_rub":50}
    op=new_operation(db,"create",{"fields":fields,"image":"cG5n"},{"cost_rub":"50"})
    db.put("operation",op["id"],{"state":"QUEUED"})
    published=[]
    def create(self,data,image,before_publish=None):
        before_publish("draft",{"id":"premium","price":13})
        published.append(True)
        return {"item_id":"draft","slug":"gift","premium":True,"premium_price":13}
    monkeypatch.setattr(Playerok,"create",create)
    svc=Service(db);svc.execute(op["id"])
    assert bool(published)==success
    if success:
        assert db.get("operation",op["id"])["state"]=="COMPLETE"
        assert db.get("ledger",op["id"]+":promotion")["amount_rub"]=="13"
        assert db.get("lot","draft")["premium"] is True
    else:
        assert db.get("operation",op["id"])["state"]=="UNKNOWN"
        assert svc.daily_spent("promotion")==50
