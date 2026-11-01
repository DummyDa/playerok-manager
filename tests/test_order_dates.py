from backend.app.order_dates import newest_orders, order_created_at


def test_polling_does_not_change_order_activity(db,monkeypatch):
    from backend.app.order_dates import order_activity_at
    order=db.put("order","o",{"created_at":10,"stage":"DISCOVERED","playerok_status":"PAID"})
    original=order["updated_at"]
    monkeypatch.setattr("backend.app.db.now",lambda:100)
    assert db.put("order","o",{"playerok_status":"PAID"})["updated_at"]==original
    assert order_activity_at(order)==10
    changed=db.put("order","o",{"stage":"MANUAL","reason":"Needs review"})
    assert changed["activity_at"]==100 and order_activity_at(changed)==100
    db.put("order","o",{"playerok_status":"SENT"})
    assert order_activity_at(db.get("order","o"))==100
    assert order_activity_at({"created_at":10,"updated_at":999},[{"completed_at":20}])==20


def test_orders_sort_by_creation_not_last_sync_or_state():
    orders=[{"id":"new","created_at":"2026-10-29T12:00:00Z","updated_at":1,"stage":"COMPLETE"},
            {"id":"old","created_at":"2026-10-28T12:00:00Z","updated_at":9999999999,"stage":"PAID"}]
    assert [o["id"] for o in newest_orders(orders)]==["new","old"]


def test_existing_playerok_ids_sort_without_dates_or_external_requests():
    older={"id":"1f195a43-9c51-6af0-a3d5-d0352b1d3858","discovered_at":9999999999}
    newer={"id":"1f1d3c6b-1ef6-6e80-520e-8ce4b21f0952","discovered_at":1}
    assert order_created_at(newer)>order_created_at(older)
    assert newest_orders([older,newer])==[newer,older]


def test_local_records_use_discovery_time_as_fallback():
    assert order_created_at({"id":"local","created_at":"invalid","discovered_at":123})==123


def test_orders_api_keeps_newest_first_when_old_order_is_updated(db):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    db.put("order","new",{"created_at":"2026-10-29T12:00:00Z","stage":"COMPLETE"})
    db.put("order","old",{"created_at":"2026-10-28T12:00:00Z","stage":"MANUAL"})
    with TestClient(create_app(db)) as client:
        client.post("/api/v1/auth/setup",json={"password":"ordering-test-password"})
        assert [o["id"] for o in client.get("/api/v1/orders").json()["items"]]==["new","old"]
