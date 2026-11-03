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
