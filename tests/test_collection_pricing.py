import time
from unittest.mock import Mock

import pytest

from backend.app.integrations.clients import Mrkt
from backend.app.pricing import D, CollectionQuoteRequired
from backend.app.pricing_scope import quote_key
from backend.app.safety import is_nft_lot
from backend.app.service import Service


def configure(db):
    db.configure(ton_rate_mode="manual", manual_ton_rub="100", stars_rub="1", transfer_stars_reserve=25)
    db.put("fees", "default", {"configured": True, "fees": []})
    db.put("lot", "ad", {"delivery_mode": "catalog", "catalog_collection": "Chill Flame",
        "lot_type": "PROMOTIONAL", "status": "APPROVED", "price": 500,
        "strategy": {"enabled": True, "profit_type": "rub", "profit_value": 50, "round_step": 1}})
    service = Service(db)
    service.premium_quote = Mock(return_value={"cost_rub": "20", "checked_at": time.time(), "price": 295})
    service.mrkt = Mock()
    service.mrkt.search.return_value = [{"id": "gift", "salePrice": 2000000000, "nextTransferDate": None}]
    service.mrkt.ton.return_value = "2"
    return service


def test_collection_search_has_no_model_filter_and_rejects_unavailable_gifts(db, monkeypatch):
    mrkt = Mrkt(db)
    def offer(id, price, model, **extra):
        return {"id": id, "salePrice": price, "collectionName": "Chill Flame", "modelName": model,
                "nextTransferDate": None, "giftType": "Upgraded", **extra}
    request = Mock(return_value={"gifts": [offer("expensive", 200, "Ghost Light"),
        offer("cheap", 100, "Camelot"), offer("locked", 1, "Obsidian", isLocked=True),
        offer("foreign", 2, "Ghost Light", collectionName="Other")]})
    monkeypatch.setattr(mrkt, "request", request)
    assert [g["id"] for g in mrkt.search("Chill Flame")] == ["cheap", "expensive"]
    assert request.call_args.args[2]["modelNames"] == []
    assert [g["id"] for g in mrkt.search("Chill Flame", "Ghost Light")] == ["expensive"]
    assert request.call_args.args[2]["modelNames"] == ["Ghost Light"]


def test_collection_auto_price_includes_transfer_profit_and_one_premium(db):
    service = configure(db)
    result = service.estimate("ad")
    service.mrkt.search.assert_called_once_with("Chill Flame", "")
    assert D(result["purchase_rub"]) == 200
    assert result["sale_price"] == 295  # 200 + 25 + 50 + 20
    assert result["pricing_scope"] == "collection" and result["pair_id"] is None
    assert result["premium_count"] == 1 and D(result["promotion_rub"]) == 20
    assert not is_nft_lot(db.get("lot", "ad"))
    service.reprice(apply=False)
    assert db.get("estimate", "ad")["status"] == "READY"


def test_collection_cache_is_distinct_from_model_and_read_only_preview_never_fetches(db):
    service = configure(db)
    db.put("pair", "pair", {"collection": "Chill Flame", "model": "Ghost Light"})
    db.put("quote", "pair", {"available": True, "ton": "99", "fetched_at": time.time()})
    with pytest.raises(CollectionQuoteRequired):
        service.estimate("ad", allow_network=False)
    service.mrkt.search.assert_not_called()
    service.estimate("ad")
    result = service.estimate("ad", allow_network=False)
    assert result["quote_ton"] == "2"
    assert db.get("quote", "pair")["ton"] == "99"


def test_collection_quotes_follow_schedule_and_do_not_create_empty_model_pairs(db):
    service = configure(db)
    key = quote_key(db.get("lot", "ad"))
    service.quotes()
    assert service.refresh_quote(key, scheduled=True)["available"]
    assert db.get("quote", key)["ton"] == "2"
    assert db.list("pair") == []


def test_collection_automatic_repricing_updates_listing_without_buying_nft(db, monkeypatch):
    service = configure(db)
    db.configure(repricing_enabled=True)
    client = Mock()
    client.update.return_value = {"id": "ad", "name": "Ad", "price": 295}
    monkeypatch.setattr("backend.app.service.Playerok", lambda _: client)
    service.reprice()
    operation = next(o for o in db.list("operation") if o["action"] == "price")
    service.execute(operation["id"])
    assert db.get("operation", operation["id"])["state"] == "COMPLETE"
    client.update.assert_called_once_with("ad", {"price": 295})
    service.mrkt.buy.assert_not_called()


def test_http_preview_schedules_worker_for_uncached_collection(db):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    configure(db)
    with TestClient(create_app(db)) as client:
        response = client.post("/api/v1/auth/setup", json={"password": "long-test-password"})
        headers = {"X-CSRF-Token": response.json()["csrf"]}
        response = client.post("/api/v1/pricing/ad/preview", headers=headers)
        assert response.status_code == 200 and response.json()["task_id"]
