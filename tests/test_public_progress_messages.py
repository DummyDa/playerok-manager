from catalog.public_messages import buyer_message,public_data,GENERIC,STATUS_MESSAGES


def test_progress_is_not_reported_as_an_error():
    for value,expected in STATUS_MESSAGES.items():
        assert buyer_message(value)==expected
        assert GENERIC not in expected
        assert public_data({"notice":value})["notice"]==expected


def test_progress_prefix_does_not_expose_internal_errors():
    for value in STATUS_MESSAGES:
        assert buyer_message(value+" token=private")==GENERIC
    assert buyer_message("MRKT: HTTP 500 secret")==GENERIC
