import json
import unittest
from unittest.mock import Mock, patch

import monitor


def config():
    return {
        "storefront": "us",
        "postal_code": "97204",
        "stores": {"R077": "Pioneer Place", "R090": "Washington Square"},
        "products": [
            {"name": "Remote", "part": "MW5G3AM/A", "url": "https://www.apple.com/shop/product/MW5G3AM/A"},
            {"name": "Cable", "part": "ABC123/A", "url": "https://www.apple.com/shop/product/ABC123/A"},
        ],
        "interval_seconds": 120,
        "reminder_seconds": 900,
    }


class ConfigTests(unittest.TestCase):
    def test_accepts_general_products_and_returns_config(self):
        value = config()
        self.assertIs(monitor.validate_config(value), value)
        value["products"][0]["url"] = "https://www.apple.com/shop/buy-airtag/airtag"
        self.assertIs(monitor.validate_config(value), value)

    def test_rejects_bad_numeric_values_and_duplicate_parts(self):
        for value in (0, -1, 1.5, float("inf"), True, "120"):
            with self.subTest(value=value):
                bad = config()
                bad["interval_seconds"] = value
                with self.assertRaises(ValueError):
                    monitor.validate_config(bad)
        bad = config()
        bad["products"][1]["part"] = bad["products"][0]["part"]
        with self.assertRaises(ValueError):
            monitor.validate_config(bad)

    def test_rejects_url_that_can_escape_apple_product_boundary(self):
        for url in (
            "http://www.apple.com/shop/product/A",
            "https://evil.example/shop/product/A",
            "https://www.apple.com:444/shop/product/A",
            "https://user@www.apple.com/shop/product/A",
            "https://www.apple.com/shop/product/A?x=1",
            "https://www.apple.com/shop/checkout",
        ):
            with self.subTest(url=url):
                bad = config()
                bad["products"][0]["url"] = url
                with self.assertRaises(ValueError):
                    monitor.validate_config(bad)

    def test_rejects_non_us_storefront_and_non_object(self):
        with self.assertRaises(ValueError):
            monitor.validate_config([])
        bad = config()
        bad["storefront"] = "ca"
        with self.assertRaisesRegex(ValueError, "us"):
            monitor.validate_config(bad)


class PickupParserTests(unittest.TestCase):
    def test_parses_only_requested_stores_and_known_english_statuses(self):
        result = monitor.parse_pickup({"body": {"content": [
            {"storeId": "R077", "pickupSearchQuote": "Available today"},
            {"storeId": "R090", "pickupSearchQuote": "Currently unavailable"},
            {"storeId": "R999", "pickupSearchQuote": "Available"},
        ]}}, config()["stores"])
        self.assertEqual(result, {"R077": (True, "Available today"), "R090": (False, "Currently unavailable")})

    def test_rejects_missing_duplicate_unknown_and_malformed_quotes(self):
        cases = [
            {"body": {"content": [{"storeId": "R077", "pickupSearchQuote": "Available"}]}},
            {"body": {"content": [
                {"storeId": "R077", "pickupSearchQuote": "Available"},
                {"storeId": "R077", "pickupSearchQuote": "Available"},
                {"storeId": "R090", "pickupSearchQuote": "Unavailable"},
            ]}},
            {"body": {"content": [
                {"storeId": "R077", "pickupSearchQuote": "AvailableSoon??"},
                {"storeId": "R090", "pickupSearchQuote": "Unavailable"},
            ]}},
            {"body": {"content": "bad"}},
        ]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(ValueError):
                monitor.parse_pickup(response, config()["stores"])


class QueryTests(unittest.TestCase):
    def test_warms_each_product_then_confirms_postal_code_then_queries(self):
        calls = []
        session_options = []

        class Response:
            status = 200
            url = "https://www.apple.com/shop/"
            headers = {}

            def __init__(self, body=None):
                self.body = body or {}

            def json(self):
                return self.body

        class Session:
            def __init__(self, **kwargs):
                self.options = kwargs
                session_options.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def get(self, url, **kwargs):
                calls.append((url, kwargs))
                if url.endswith("location/update"):
                    return Response({"body": {"content": {"address": {"postalCode": "97204"}}}})
                if url.endswith("pickup-detail"):
                    return Response({"body": {"content": [
                        {"storeId": "R077", "pickupSearchQuote": "Available"},
                        {"storeId": "R090", "pickupSearchQuote": "Unavailable"},
                    ]}})
                return Response()

        answer = monitor.query_product(config()["products"][0], "97204", config()["stores"], Session)
        self.assertTrue(answer["R077"][0])
        self.assertEqual([url for url, _ in calls], [
            "https://www.apple.com/shop/product/MW5G3AM/A",
            "https://www.apple.com/shop/address/location/update",
            "https://www.apple.com/shop/sba/pickup-detail",
        ])
        self.assertEqual(session_options[0], {
            "timeout": 15, "retries": 0, "follow_redirects": False,
            "stealthy_headers": False, "impersonate": None,
            "headers": {"User-Agent": "AppleStockBark/1.0"},
        })

    def test_postal_mismatch_stops_before_inventory_query(self):
        calls = []

        class Response:
            status = 200
            headers = {}

            def json(self):
                return {"body": {"content": {"address": {"postalCode": "97205"}}}}

        class Session:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def get(self, url, **kwargs):
                calls.append(url)
                return Response()

        with self.assertRaisesRegex(monitor.AppleError, "did not confirm"):
            monitor.query_product(config()["products"][0], "97204", config()["stores"], Session)
        self.assertEqual(len(calls), 2)


class BarkTests(unittest.TestCase):
    def test_endpoint_requires_one_key_path_and_normalizes_trailing_slash(self):
        self.assertEqual(monitor.bark_endpoint("https://bark.example/key/"), "https://bark.example/key")
        for value in ("https://bark.example", "https://bark.example/a/b", "https://u@bark.example/key",
                      "https://bark.example/key?x=1", "https://bark.example/key#frag", "https://bark.example/key\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                monitor.bark_endpoint(value)

    def test_sends_json_to_keyed_endpoint_and_rejects_non_200_api_code(self):
        seen = []

        class Response:
            def __init__(self, code):
                self.code = code

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({"code": self.code}).encode()

        def open_request(request, timeout):
            seen.append((request, timeout))
            return Response(200)

        with patch.object(monitor.urllib.request, "urlopen", side_effect=open_request):
            monitor.send_bark("https://bark.example/key", "Title", "Body", "https://www.apple.com/shop/product/A")
        request, timeout = seen[0]
        self.assertEqual(request.full_url, "https://bark.example/key")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {
            "title": "Title", "body": "Body", "group": "Apple Stock", "level": "timeSensitive",
            "url": "https://www.apple.com/shop/product/A",
        })
        self.assertEqual(timeout, 15)
        with patch.object(monitor.urllib.request, "urlopen", return_value=Response(400)):
            with self.assertRaises(monitor.BarkError):
                monitor.send_bark("https://bark.example/key", "Title", "Body")


class RunTests(unittest.TestCase):
    def test_once_queries_all_products_without_bark_and_returns_stock_code(self):
        query = Mock(side_effect=[
            {"R077": (False, "Unavailable"), "R090": (False, "Unavailable")},
            {"R077": (True, "Available"), "R090": (False, "Unavailable")},
        ])
        with patch.object(monitor, "send_bark", side_effect=AssertionError("once must not notify")) as bark:
            self.assertEqual(monitor.run_once(config(), query=query, sleep_fn=Mock()), 10)
        self.assertEqual(query.call_count, 2)
        bark.assert_not_called()

    def test_failed_bark_does_not_advance_alert_cooldown(self):
        state = {}
        failing = Mock(side_effect=[OSError("secret-key-must-not-be-logged"), True])
        available = {"R077": (True, "Available"), "R090": (False, "Unavailable")}
        self.assertFalse(monitor.notify_available(config(), config()["products"][0], available, state,
                                                  "https://bark.example/key", failing, now=100))
        self.assertNotIn(("MW5G3AM/A", "R077"), state)
        self.assertTrue(monitor.notify_available(config(), config()["products"][0], available, state,
                                                 "https://bark.example/key", failing, now=100))
        self.assertEqual(state[("MW5G3AM/A", "R077")], 100)

    def test_success_sets_cooldown_and_confirmed_unavailable_resets_it(self):
        state = {}
        bark = Mock()
        value = config()
        available = {"R077": (True, "Available"), "R090": (False, "Unavailable")}
        self.assertTrue(monitor.notify_available(value, value["products"][0], available, state,
                                                 "https://bark.example/key", bark, now=100))
        self.assertTrue(monitor.notify_available(value, value["products"][0], available, state,
                                                 "https://bark.example/key", bark, now=200))
        bark.assert_called_once()
        unavailable = {"R077": (False, "Unavailable"), "R090": (False, "Unavailable")}
        monitor.notify_available(value, value["products"][0], unavailable, state,
                                 "https://bark.example/key", bark, now=201)
        self.assertEqual(state, {})

    def test_apple_rate_limit_stops_cycle_and_honors_retry_after(self):
        calls = []
        def query(product, postal, stores):
            calls.append(product["part"])
            raise monitor.AppleError("Apple HTTP 429", 429, 1800)
        def sleep(seconds):
            self.assertEqual(seconds, 1800)
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            monitor.run_forever(config(), "https://bark.example/key", query=query, sleep_fn=sleep)
        self.assertEqual(calls, ["MW5G3AM/A"])


if __name__ == "__main__":
    unittest.main()
