import json
import unittest
from unittest.mock import Mock,patch
import server


class BrowserTests(unittest.TestCase):
    def setUp(self):
        server.cache.clear();server.next_request=0;server.blocked_until=0
        self.node={"id":"one","slug":"lot-one","priority":"DEFAULT","status":"APPROVED","name":"Обычный лот","price":100,"rawPrice":200,"attachment":{"url":"https://i.playerok.com/test.jpg"},"user":{"rating":4.8,"testimonialCounter":125}}

    def test_only_explicit_approved_default_items_are_visible(self):
        self.assertEqual(server.normalize(self.node)["price"],100)
        for values in [{"priority":"PREMIUM"},{"priority":None},{"priority":"UNKNOWN"},{"status":"SOLD"}]:
            self.assertIsNone(server.normalize({**self.node,**values}))

    def test_filter_pagination_and_cache_use_only_public_get(self):
        response=Mock(status_code=200)
        response.json.return_value={"data":{"items":{"edges":[{"node":self.node},{"node":{**self.node,"priority":"PREMIUM"}}],"pageInfo":{"hasNextPage":True,"endCursor":"next"}}}}
        with patch.object(server.requests,"get",return_value=response) as get:
            first=server.load_page();second=server.load_page()
        get.assert_called_once()
        self.assertEqual(len(first["items"]),1);self.assertEqual(first,second)
        variables=json.loads(get.call_args.kwargs["params"]["variables"])
        self.assertEqual(variables["filter"]["priority"],"DEFAULT")
        self.assertEqual(variables["filter"]["status"],["APPROVED"])
        self.assertNotIn("cookies",get.call_args.kwargs)
        self.assertEqual(first["next_cursor"],"next")

    def test_invalid_images_and_unexpected_priority_are_not_trusted(self):
        bad={**self.node,"attachment":{"url":"javascript:alert(1)"},"rawPrice":50}
        value=server.normalize(bad)
        self.assertIsNone(value["image"]);self.assertIsNone(value["old_price"])
        self.assertIsNone(server.normalize({**self.node,"slug":"../bad"}))

    def test_rate_limit_is_not_hammered_and_invalid_cursor_is_rejected(self):
        with patch.object(server.requests,"get",return_value=Mock(status_code=429)) as get:
            with self.assertRaises(RuntimeError):server.load_page()
            with self.assertRaises(RuntimeError):server.load_page()
            get.assert_called_once()
        with self.assertRaises(ValueError):server.load_page("' OR 1=1")


if __name__=="__main__":unittest.main()
