from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from backend.app.integrations.clients import Playerok, IntegrationError, order_recipient


def deal(*fields, status="PAID", direction="OUT", id="order"):
    return NS(id=id, direction=direction, status=status, obtaining_fields=list(fields),
              item=NS(id="lot", name="Gift",slug="gift-listing",price=100), price=100)


@pytest.fixture
def playerok(db, monkeypatch):
    db.secret("playerok_cookies", "test-cookie-value")
    adapter = Playerok(db)
    account = Mock()
    monkeypatch.setattr(adapter, "account", lambda: account)
    return adapter, account


def test_paid_list_summary_loads_username_from_full_deal(playerok):
    adapter, account = playerok
    account.get_deals.return_value = NS(deals=[deal()])
    account.get_deal.return_value = deal(NS(label="Введите @username", value="@Ramz_02"))
    result = adapter.orders()
    assert result[0]["recipient"] == "Ramz_02"
    account.get_deal.assert_called_once_with("order")


def test_complete_list_entry_does_not_fetch_again(playerok):
    adapter, account = playerok
    account.get_deals.return_value = NS(deals=[deal(NS(label="username", value="Ramz_02"))])
    assert adapter.orders()[0]["recipient"] == "Ramz_02"
    account.get_deal.assert_not_called()


def test_direct_lookup_uses_details_even_for_completed_order(playerok):
    adapter, account = playerok
    account.get_deal.return_value = deal(NS(label="Введите @username", value="@Ramz_02"),status="CONFIRMED")
    assert adapter.orders(deal_id="order")[0]["recipient"] == "Ramz_02"
    account.get_deals.assert_not_called()


def test_historical_summaries_do_not_load_each_detail(playerok):
    adapter, account = playerok
    account.get_deals.return_value = NS(deals=[deal(status="CONFIRMED"),deal(direction="IN")])
    assert len(adapter.orders()) == 1
    account.get_deal.assert_not_called()


def test_chat_summary_fallback_also_loads_full_deal(playerok):
    adapter, account = playerok
    account.get_deals.side_effect = RuntimeError("List unavailable")
    account.get_chats.return_value = NS(chats=[NS(deals=[deal()])])
    account.get_deal.return_value = deal(NS(label="username",value="@Ramz_02"))
    assert adapter.orders()[0]["recipient"] == "Ramz_02"


@pytest.mark.parametrize("detail", [None,deal(id="other"),deal(direction="IN")])
def test_detail_must_match_order_and_direction(playerok, detail):
    adapter, account = playerok
    account.get_deals.return_value = NS(deals=[deal()])
    account.get_deal.return_value = detail
    with pytest.raises(IntegrationError):
        adapter.orders()


@pytest.mark.parametrize("value", ["buyer@example.com", "", "@one @two", "123456789", "https://example.com"])
def test_invalid_field_is_not_used_as_recipient(value):
    assert order_recipient(deal(NS(label="Введите @username",value=value))) is None


def test_conflicting_usernames_are_not_automatically_selected():
    assert order_recipient(deal(NS(label="username",value="@BuyerOne"),NS(label="username",value="@BuyerTwo"))) is None


def test_automation_fields_supported_and_duplicate_casing_is_not_conflict():
    d = deal(NS(label="username",value="@Ramz_02"))
    d.automation_obtaining_fields = [NS(code="telegram_username",name="Telegram",value="ramz_02")]
    assert order_recipient(d) == "Ramz_02"


def test_detail_failure_propagates_without_using_incomplete_recipient(playerok):
    adapter, account = playerok
    account.get_deals.return_value = NS(deals=[deal()])
    account.get_deal.side_effect = IntegrationError("Details unavailable")
    with pytest.raises(IntegrationError):
        adapter.orders()


def test_sold_snapshot_resolves_original_listing_and_uses_item_price(db, playerok):
    adapter, account = playerok
    db.put("lot","original",{"slug":"gift-listing","pair_id":"p","delivery_mode":"telegram_nft"})
    detail=deal(NS(label="username",value="@buyer"))
    detail.item.id="sold-copy"
    detail.item.price=565
    del detail.price
    detail.chat=NS(id="chat-id")
    detail.transaction=NS(value=508.5)
    account.get_deal.return_value=detail
    result=adapter.orders(deal_id="order")[0]
    assert result["lot_id"]=="original" and result["source_item_id"]=="sold-copy"
    assert result["sale_rub"]=="565" and result["chat_id"]=="chat-id"


def test_slug_match_must_be_unique_and_does_not_override_manual_binding(db):
    from backend.app.integrations.clients import order_lot_id
    item=NS(id="snapshot",slug="same")
    db.put("lot","one",{"slug":"same","pair_id":"p"})
    db.put("lot","two",{"slug":"same","pair_id":"p"})
    assert order_lot_id(db,item)=="snapshot"
    db.put("lot","snapshot",{"delivery_mode":"manual"})
    assert order_lot_id(db,item)=="snapshot"


@pytest.mark.parametrize("blocked", [None,"hold","operation","purchased","legacy"])
def test_only_unstarted_system_classification_can_resume_after_exact_mapping(db,monkeypatch,blocked):
    from backend.app.service import Service
    from sqlalchemy import select
    from backend.app.db import Task
    db.configure(orders_baselined=True)
    db.put("lot","original",{"pair_id":"p","delivery_mode":"telegram_nft"})
    old={"lot_id":"sold-copy","stage":"MANUAL",
         "reason":"Обычный товар: закупка MRKT и автоматическая выдача не применяются"}
    if blocked=="hold":old["manual_hold"]=True
    if blocked=="purchased":old["gift_slug"]="Gift-1"
    if blocked=="legacy":old["source"]="legacy"
    db.put("order","order",old)
    if blocked=="operation":db.put("operation","order:buy",{"state":"UNKNOWN"})
    fresh={"id":"order","lot_id":"original","source_item_id":"sold-copy","status":"PAID",
           "title":"Gift","sale_rub":"565","recipient":"buyer"}
    monkeypatch.setattr("backend.app.service.Playerok",lambda _:NS(orders=lambda:[fresh]))
    Service(db).orders()
    saved=db.get("order","order")
    assert saved["stage"]==("MANUAL" if blocked else "DISCOVERED")
    with db.sessions() as session:
        tasks=list(session.scalars(select(Task).where(Task.kind=="process_order")))
    assert len(tasks)==(0 if blocked else 1)
