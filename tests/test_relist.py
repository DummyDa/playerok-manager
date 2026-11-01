import pytest
from unittest.mock import Mock
from backend.app.service import Service
from backend.app.operations import new_operation


@pytest.mark.parametrize("failure",[None,"price_changed","disabled","budget","balance","timeout"])
def test_premium_relist_uses_confirmed_tariff_and_records_single_expense(db,monkeypatch,failure):
    db.configure(playerok_account_id="seller",promotions_enabled=True)
    db.put("lot","l",{"slug":"gift","status":"SOLD","price":600,"pair_id":"p","keep_in_sale":True})
    client=Mock()
    client.item.return_value={"id":"l","status":"SOLD","price":600,"user":{"id":"seller"}}
    client.viewer.return_value={"balance":{"available":100}}
    client.client.get_priority_status.return_value={"id":"premium","price":25}
    client.client.publish_item.return_value={"id":"l","slug":"gift","price":600,"status":"APPROVED","premium":True}
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:client)
    service=Service(db)
    op=service.preview_relist("l",True)
    assert op["expected"]["cost_rub"]=="25"
    client.client.publish_item.assert_not_called()
    if failure=="price_changed":client.client.get_priority_status.return_value={"id":"premium","price":37}
    if failure=="disabled":db.configure(promotions_enabled=False)
    if failure=="budget":db.configure(daily_promotion_limit_rub="20")
    if failure=="balance":client.viewer.return_value={"balance":{"available":10}}
    if failure=="timeout":client.client.publish_item.side_effect=TimeoutError("unknown")
    db.put("operation",op["id"],{"state":"QUEUED"})
    service.execute(op["id"]);service.execute(op["id"])
    if failure:
        assert db.get("operation",op["id"])["state"]==("UNKNOWN" if failure=="timeout" else "FAILED")
        assert client.client.publish_item.call_count==(1 if failure=="timeout" else 0)
        assert not db.list("ledger")
    else:
        assert db.get("operation",op["id"])["state"]=="COMPLETE"
        client.client.publish_item.assert_called_once_with("l","premium",premium=True,keep_in_sale=True)
        assert db.list("ledger")[0]["amount_rub"]=="25"


@pytest.mark.parametrize("status",["SOLD","DISCONTINUED"])
def test_relist_keeps_binding_and_never_retries_uncertain_publish(db,monkeypatch,status):
    db.configure(playerok_account_id="seller")
    db.put("lot","l",{"slug":"old","status":status,"pair_id":"p","description":"original description"})
    client=Mock();client.item.return_value={"id":"l","status":status,"price":600,"user":{"id":"seller"}}
    client.client.get_priority_status.return_value={"id":"free","price":0}
    client.client.publish_item.return_value={"id":"l","slug":"old","name":"Gift","price":600,"status":"PENDING_APPROVAL"}
    monkeypatch.setattr("backend.app.service.Playerok",lambda db:client)
    op=new_operation(db,"relist",{"lot_id":"l","price":600,"keep_in_sale":True})
    db.put("operation",op["id"],{"state":"QUEUED"})
    service=Service(db);service.execute(op["id"])
    assert db.get("operation",op["id"])["state"]=="COMPLETE"
    assert db.get("lot","l")["pair_id"]=="p" and db.get("lot","l")["description"]=="original description"
    client.client.publish_item.assert_called_once_with("l","free",premium=False,keep_in_sale=True)
    failed=new_operation(db,"relist",{"lot_id":"l","price":600})
    db.put("operation",failed["id"],{"state":"QUEUED"})
    client.client.publish_item.side_effect=TimeoutError("unknown result")
    service.execute(failed["id"]);service.execute(failed["id"])
    assert db.get("operation",failed["id"])["state"]=="UNKNOWN"
    assert client.client.publish_item.call_count==2
    again=new_operation(db,"relist",{"lot_id":"l","price":600})
    db.put("operation",again["id"],{"state":"QUEUED"})
    service.execute(again["id"])
    assert db.get("operation",again["id"])["state"]=="FAILED"
    assert client.client.publish_item.call_count==2
