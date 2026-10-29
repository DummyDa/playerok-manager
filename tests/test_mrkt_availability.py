from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from backend.app.integrations.clients import Mrkt, ManualRequired
from backend.app.integrations.mrkt_availability import gift_block_reason
from backend.app.service import Service


NOW = datetime(2026, 10, 29, tzinfo=timezone.utc).timestamp()


def offer(id="ready", price=100, **fields):
    return {"id":id, "collectionName":"Hypno Lollipop", "modelName":"Candy", "salePrice":price,
            "nextTransferDate":None, "isLocked":False, "giftType":"Upgraded", **fields}


@pytest.mark.parametrize("fields", [
    {"nextTransferDate":"2026-11-09T00:00:00Z"},
    {"nextTransferDate":"2026-10-29T04:00:00+03:00"},
    {"nextTransferDate":"2026-11-09T00:00:00"},
    {"returnLockedUntil":"2026-11-09T00:00:00Z"},
    {"isLocked":True}, {"premarketStatus":"WaitingGift"}, {"isOnPlatform":False},
    {"nextTransferDate":"unknown"}, {"nextTransferDate":123}, {"giftType":"Regular"},
])
def test_unavailable_or_unverifiable_gift_is_rejected(fields):
    assert gift_block_reason(offer(**fields), now=NOW)


def test_missing_transfer_availability_is_not_assumed_safe():
    gift = offer()
    del gift["nextTransferDate"]
    assert gift_block_reason(gift, now=NOW)


@pytest.mark.parametrize("date", [None, "", "0001-01-01T00:00:00", "2026-10-28T23:00:00Z", "2026-10-29T03:00:00+03:00"])
def test_absent_or_expired_timer_is_available(date):
    assert gift_block_reason(offer(nextTransferDate=date), now=NOW) is None


def test_blockchain_export_and_giveaway_timers_do_not_block_telegram_transfer():
    assert gift_block_reason(offer(exportDate="2026-11-09T00:00:00Z", unlockDate="2026-11-09T00:00:00Z",
                                   nextGiveAvailableAt="2026-11-09T00:00:00Z"), now=NOW) is None


def test_search_requests_transferable_and_skips_cheaper_locked_gifts(db, monkeypatch):
    mrkt = Mrkt(db)
    request = Mock(return_value={"gifts":[offer("locked",1,nextTransferDate="2099-01-01T00:00:00Z"),
        offer("flag",2,isLocked=True), offer("ready",100), offer("expensive",200)]})
    monkeypatch.setattr(mrkt,"request",request)
    assert [g["id"] for g in mrkt.search("Hypno Lollipop","Candy")] == ["ready","expensive"]
    payload = request.call_args.args[2]
    assert payload["isTransferable"] is True and payload["isPremarket"] is False


def test_search_continues_after_page_with_only_locked_gifts(db, monkeypatch):
    mrkt = Mrkt(db)
    request = Mock(side_effect=[{"gifts":[offer(nextTransferDate="2099-01-01T00:00:00Z")],"cursor":"next"},
                               {"gifts":[offer("ready")] }])
    monkeypatch.setattr(mrkt,"request",request)
    assert mrkt.search("Hypno Lollipop","Candy")[0]["id"] == "ready"
    assert request.call_args_list[1].args[2]["cursor"] == "next"


def test_only_locked_gifts_give_no_quote_and_do_not_disable_lot(db, monkeypatch):
    db.put("pair","p",{"collection":"Hypno Lollipop","model":"Candy"})
    db.put("lot","l",{"pair_id":"p","status":"APPROVED"})
    service = Service(db)
    monkeypatch.setattr(service.mrkt,"request",Mock(return_value={"gifts":[offer(isLocked=True)]}))
    assert service.quote("p",force=True) is None
    assert db.get("quote","p")["available"] is False
    assert db.get("lot","l")["status"] == "APPROVED"


def test_cached_locked_quote_is_refreshed(db, monkeypatch):
    import time
    db.put("pair","p",{"collection":"Hypno Lollipop","model":"Candy"})
    db.put("quote","p",{"available":True,"fetched_at":time.time(),"gift":offer(isLocked=True),"ton":"0.1"})
    service = Service(db)
    search = Mock(return_value=[offer("available",1000000000)])
    monkeypatch.setattr(service.mrkt,"search",search)
    assert service.quote("p")["gift"]["id"] == "available"
    search.assert_called_once()


@pytest.mark.parametrize("fields", [{"isLocked":True},{"nextTransferDate":"2099-01-01T00:00:00Z"}])
def test_buy_guard_stops_locked_gift_before_any_request(db, monkeypatch, fields):
    db.configure(purchases_enabled=True)
    mrkt = Mrkt(db)
    request = Mock()
    monkeypatch.setattr(mrkt,"request",request)
    with pytest.raises(ManualRequired,match="Закупка остановлена"):
        mrkt.buy(offer(**fields))
    request.assert_not_called()


def test_buy_allows_verified_available_gift(db, monkeypatch):
    db.configure(purchases_enabled=True)
    mrkt = Mrkt(db)
    request = Mock(side_effect=[{"ids":["ready"]},offer(name="Gift-1",isMine=True,isOnPlatform=True)])
    monkeypatch.setattr(mrkt,"request",request)
    assert mrkt.buy(offer(name="Gift-1"))["id"] == "ready"
    assert request.call_count == 2
