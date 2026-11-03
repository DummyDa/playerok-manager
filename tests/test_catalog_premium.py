from unittest.mock import Mock

from backend.app import catalog_premium as premium
from backend.app.catalog_orders import grouped_orders
from backend.app.catalog_credit import adjust_snapshot
from backend.app.pricing import D


def setup_sale(db, cost="35"):
    db.put("lot", "ad", {"lot_type": "PROMOTIONAL"})
    db.put("catalog_credit_payment", "wallet", {"source": "playerok_promotional", "state": "DELETED",
        "paid_rub": "700", "revision": 1, "selected_price": "700", "fulfillment_order_id": "delivery"})
    db.put("order", "paid", {"lot_id": "ad", "created_at": 10, "catalog_root_credit_id": "wallet"})
    db.put("catalog_credit_receipt", "paid", {"credit_id": "wallet", "kind": "ORIGINAL", "accepted": True,
        "lot_id": "ad", "amount_rub": "700", "revision": 0, **({"premium_rub": cost} if cost is not None else {})})
    db.put("order", "delivery", {"catalog_credit_id": "wallet", "created_at": 20, "stage": "COMPLETE"})
    return [{"id": "revenue", "type": "revenue", "order_id": "delivery", "amount_rub": "700"},
            {"id": "purchase", "type": "purchase", "order_id": "delivery", "amount_rub": "459.2"},
            {"id": "transfer", "type": "transfer", "order_id": "delivery", "amount_rub": "32.75"},
            {"id": "fees", "type": "fees", "order_id": "delivery", "amount_rub": "135.94"}]


def test_advertising_tariff_is_deducted_once_without_inventing_charge(db):
    ledger = setup_sale(db)
    projected = premium.projected_ledger(db, ledger)
    assert len(premium.projected_ledger(db, projected)) == len(projected)
    assert db.list("ledger") == []  # Allocations are not additional real payments.
    group = grouped_orders(db, db.list("order"), projected)[0]
    assert D(group["totals"]["promotion"]) == 35
    assert D(group["profit_rub"]) == D("37.11")
    snapshot = adjust_snapshot(db, "wallet", {"purchase_rub": "459.2", "transfer_rub": "32.75", "ton_rub": "100"}, {"fees": []})
    assert snapshot["paid_promotion_rub"] == "35"
    assert D(snapshot["actual_profit_rub"]) == D("173.05")


def test_missing_tariff_is_not_reported_as_zero(db):
    ledger = setup_sale(db, None)
    group = grouped_orders(db, db.list("order"), ledger)[0]
    assert group["premium_pending"] and group["profit_rub"] is None
    service = Mock(db=db)
    service.premium_quote.return_value = {"cost_rub": "40"}
    premium.refresh(service)
    service.premium_quote.assert_called_once_with("ad", "700")
    assert premium.order_cost(db, "delivery") == 40
    service.premium_quote.reset_mock()
    premium.refresh(service)
    service.premium_quote.assert_not_called()


def test_automatic_lots_and_topups_have_no_premium(db):
    setup_sale(db)
    db.put("lot", "ad", {"lot_type": "TEMPORARY", "catalog_checkout_id": "checkout"})
    assert premium.allocations(db) == []
    db.put("lot", "ad", {"lot_type": "PROMOTIONAL", "catalog_checkout_id": None})
    db.put("catalog_credit_receipt", "paid", {"kind": "TOPUP"})
    assert premium.allocations(db) == []


def test_failed_attempt_and_later_gifts_do_not_repeat_premium(db):
    setup_sale(db)
    db.put("order", "failed", {"catalog_credit_id": "wallet", "created_at": 15, "stage": "MANUAL"})
    db.put("order", "later", {"catalog_credit_id": "wallet", "created_at": 30, "stage": "COMPLETE"})
    assert premium.order_cost(db, "delivery") == 35
    assert premium.order_cost(db, "failed") == premium.order_cost(db, "later") == 0


def test_each_new_advertising_sale_has_its_own_tariff(db):
    setup_sale(db)
    db.put("order", "paid-again", {"lot_id": "ad", "created_at": 30})
    db.put("catalog_credit_receipt", "paid-again", {"credit_id": "wallet", "kind": "ORIGINAL",
        "accepted": True, "lot_id": "ad", "amount_rub": "800", "premium_rub": "45"})
    db.put("order", "second-delivery", {"catalog_credit_id": "wallet", "created_at": 40, "stage": "COMPLETE"})
    assert premium.order_cost(db, "delivery") == 35
    assert premium.order_cost(db, "second-delivery") == 45
