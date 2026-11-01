import pytest
from backend.app.listing_prices import validate_price


@pytest.mark.parametrize("price",[500,475,400])
def test_price_reset_or_discount_allowed(price):
    validate_price({"raw_price":500},price)


@pytest.mark.parametrize("price",[540,499,480])
def test_invalid_discount_or_base_increase_rejected(price):
    with pytest.raises(ValueError):validate_price({"raw_price":500},price)
