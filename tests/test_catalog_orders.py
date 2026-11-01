from backend.app.catalog_orders import grouped_orders


def test_review_code_can_be_revoked_without_erasing_payment_history(db):
    from backend.app.catalog_credit import create,remove,valid
    wallet,_=create(db,"500","buyer")
    db.put("catalog_credit_payment",wallet["id"],{"state":"REVIEW","reservation_rub":"500","notice":"Нужна сверка"})
    removed=remove(db,db.get("catalog_credit_payment",wallet["id"]))
    assert removed["state"]=="DELETED" and not valid(removed)
    assert removed["paid_rub"]=="500" and removed["reservation_rub"]=="0"
    event=next(e for e in db.list("catalog_credit_event") if e["action"]=="DELETED")
    assert event["previous_state"]=="REVIEW" and event["reserved_before_rub"]=="500"


def test_review_shows_actual_topup_rejection_without_mutating_balance(db):
    db.put("catalog_credit_payment","wallet",{"state":"REVIEW","paid_rub":"500","initial_rub":"500","reservation_rub":"500","checkout_id":"checkout","notice":"Результат публикации лота требует сверки владельцем"})
    db.put("catalog_checkout","checkout",{"state":"UNKNOWN","topup_rub":"29","error":'Playerok HTTP error: {"errors":[{"message":"Минимальная цена - 90","extensions":{"code":"BAD_REQUEST"}}],"data":null}\n'})
    result=grouped_orders(db,[{"id":"root","catalog_root_credit_id":"wallet"}],[])[0]
    assert "29 ₽" in result["reason"] and "Минимальная цена - 90" in result["reason"]
    assert "extensions" not in result["reason"]
    assert result["stage"]=="MANUAL" and result["reserved_rub"]=="500"
    assert db.get("catalog_credit_payment","wallet")["reservation_rub"]=="500"


def test_payments_and_multiple_deliveries_are_grouped_without_double_counting(db):
    db.put("catalog_credit_payment","wallet",{"state":"AWAITING_SELECTION","source":"playerok_promotional","root_order_id":"root","paid_rub":"1200","initial_rub":"1000","recipient":"buyer","purchases":[{"price_rub":"700"},{"price_rub":"400"}],"created_at":1})
    for id,kind,amount in [("root","ORIGINAL","1000"),("topup","TOPUP","200")]:
        db.put("catalog_credit_receipt",id,{"credit_id":"wallet","kind":kind,"accepted":True,"status":"SENT","amount_rub":amount})
    rows=[{"id":"root","catalog_root_credit_id":"wallet","stage":"AWAITING_SELECTION"},{"id":"topup","catalog_topup_credit_id":"wallet"},{"id":"a","catalog_credit_id":"wallet","stage":"COMPLETE"},{"id":"b","catalog_credit_id":"wallet","stage":"COMPLETE"},{"id":"ordinary"}]
    ledger=[{"order_id":"a","type":"revenue","amount_rub":"700"},{"order_id":"a","type":"purchase","amount_rub":"500"},{"order_id":"b","type":"revenue","amount_rub":"400"},{"order_id":"b","type":"purchase","amount_rub":"300"}]
    result=grouped_orders(db,rows,ledger)
    assert len(result)==2
    group=next(r for r in result if r.get("catalog_group"))
    assert group["balance_rub"]=="100" and group["sale_rub"]=="1200"
    assert group["totals"]["revenue"]=="1100" and group["profit_rub"]=="300"
    assert len(group["deliveries"])==2 and group["delivery_count"]==2
    assert group["stage"]=="AWAITING_SELECTION"


def test_completed_and_manual_credit_refunds_are_reported_honestly(db):
    db.put("catalog_credit_payment","wallet",{"state":"EXHAUSTED","paid_rub":"100","initial_rub":"100","purchases":[{"price_rub":"100"}]})
    rows=[{"id":"a","catalog_credit_id":"wallet","stage":"COMPLETE"}]
    group=grouped_orders(db,rows,[])[0]
    assert group["stage"]=="COMPLETE" and group["manual_credit_rub"]=="100"
    assert group["paid_rub"]=="0" and group["profit_rub"] is None
    db.put("catalog_credit_payment","wallet",{"state":"REVIEW"})
    db.put("catalog_credit_receipt","refund",{"credit_id":"wallet","accepted":True,"amount_rub":"50","status":"ROLLED_BACK"})
    group=grouped_orders(db,rows,[])[0]
    assert group["stage"]=="MANUAL" and group["refunded_rub"]=="50"


def test_unused_manual_codes_do_not_clutter_orders_and_old_rows_are_retained(db):
    db.put("catalog_credit_payment","unused",{"state":"AWAITING_SELECTION","paid_rub":"100"})
    assert grouped_orders(db,[{"id":"old"}],[])==[{"id":"old"}]


def test_confirmed_transfer_with_unfinished_playerok_confirmation_is_not_complete(db):
    db.put("catalog_credit_payment","wallet",{"state":"EXHAUSTED","paid_rub":"100","fulfillment_order_id":"a","purchases":[{"order_id":"a","price_rub":"100"}]})
    group=grouped_orders(db,[{"id":"a","catalog_credit_id":"wallet","stage":"MANUAL"}],[])[0]
    assert group["stage"]=="MANUAL"


def test_orders_endpoint_groups_rows_and_keeps_group_owner_only(db):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    db.put("catalog_credit_payment","wallet",{"state":"EXHAUSTED","paid_rub":"100","purchases":[{"price_rub":"100"}],"code_encrypted":"private"})
    db.put("order","child",{"catalog_credit_id":"wallet","stage":"COMPLETE"})
    with TestClient(create_app(db)) as client:
        assert client.get("/api/v1/orders").status_code==401
        client.post("/api/v1/auth/setup",json={"password":"test-owner-password"})
        data=client.get("/api/v1/orders").json()
        assert len(data["items"])==1 and data["items"][0]["catalog_group"]
        assert data["items"][0]["deliveries"][0]["id"]=="child"
        assert "code_encrypted" not in str(data)
        assert client.get("/api/v1/dashboard").json()["orders"]==1
