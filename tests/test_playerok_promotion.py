from unittest.mock import Mock

import pytest

from backend.app.integrations.clients import Playerok
from backend.app.safety import ManualRequired


@pytest.mark.parametrize("priority", ["PREMIUM", "DEFAULT"])
def test_promotion_uses_supported_fields_and_checks_confirmation(db, priority):
    db.configure(promotions_enabled=True)
    playerok = Playerok.__new__(Playerok)
    playerok.db = db
    playerok.client = Mock()
    item = {"id": "lot", "priority": priority}
    playerok.client._graphql_post_json.return_value = {"increaseItemPriorityStatus": item}

    if priority == "PREMIUM":
        assert playerok.promote("lot", "tariff") == item
    else:
        with pytest.raises(ManualRequired):
            playerok.promote("lot", "tariff")

    playerok.client._graphql_post_json.assert_called_once()
    payload = playerok.client._graphql_post_json.call_args.args[0]
    assert "priorityPrice" not in payload["query"]
    assert payload["variables"]["input"]["priorityStatuses"] == ["tariff"]


@pytest.mark.parametrize("price,publication,bump", [(749,25,13),(1500,37,19)])
def test_publication_tariff_uses_verified_inactive_nft_not_active_bump(db,price,publication,bump):
    from backend.app.safety import IntegrationError
    db.put("lot","active",{"pair_id":"pair","status":"APPROVED","slug":"active"})
    db.put("lot","inactive",{"pair_id":"pair","status":"DISCONTINUED","slug":"inactive"})
    playerok=Playerok.__new__(Playerok);playerok.db=db;playerok.client=Mock()
    playerok.item=Mock(return_value={"id":"inactive","status":"DISCONTINUED"})
    playerok.client.get_priority_status.side_effect=lambda id,price,premium:{"id":"tariff","price":publication if id=="inactive" else bump}
    assert playerok.publication_premium("active",price)["price"]==publication
    assert playerok.premium("active",price)["price"]==bump
    playerok.item.return_value={"id":"inactive","status":"APPROVED"}
    with pytest.raises(IntegrationError):playerok.publication_premium("active",price)
