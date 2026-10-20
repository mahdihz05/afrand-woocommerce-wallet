import json
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

from panel.woo import WooCommerceClient, WooError


class WooCommerceClientTests(unittest.TestCase):
    def test_reads_have_short_timeout_without_shortening_write_timeout(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'[]'
        with patch.object(self.client.opener, 'open', return_value=response) as opener:
            self.client.request('GET', 'products')
            self.client.request('POST', 'orders', payload={})
        self.assertEqual(opener.call_args_list[0].kwargs['timeout'], 5)
        self.assertEqual(opener.call_args_list[1].kwargs['timeout'], 15)

    def test_connection_retry_is_for_reads_only(self):
        with patch.object(self.client, '_request_once', side_effect=[WooError('timeout', category='connection'), []]) as request:
            self.assertEqual(self.client.request('GET', 'products'), [])
            self.assertEqual(request.call_count, 2)
        with patch.object(self.client, '_request_once', side_effect=WooError('timeout', category='connection')) as request:
            with self.assertRaises(WooError):
                self.client.request('POST', 'orders', payload={})
            self.assertEqual(request.call_count, 1)

    def test_sku_search_uses_exact_api_filter_instead_of_name_search(self):
        with patch.object(self.client, 'request', return_value=[{'id': 1, 'sku': 'SKU-X'}, {'id': 2, 'sku': 'OTHER'}]) as request:
            found = self.client.get_products_by_sku('SKU-X')
        self.assertEqual([p['id'] for p in found], [1])
        self.assertEqual(request.call_args.kwargs['params']['sku'], 'SKU-X')
        self.assertNotIn('search', request.call_args.kwargs['params'])

    def setUp(self):
        self.client = WooCommerceClient({
            "WC_BASE_URL": "https://store.example",
            "WC_STAGING_HOST": "store.example",
            "WC_CONSUMER_KEY": "consumer-key",
            "WC_CONSUMER_SECRET": "consumer-secret",
        })

    def test_customer_update_uses_put_and_preserves_address_contacts(self):
        existing = {
            "id": 12,
            "first_name": "Sara",
            "last_name": "Old",
            "email": "sara@example.test",
            "billing": {"first_name": "Billing", "email": "billing@example.test", "phone": "09125556677"},
            "shipping": {"first_name": "Shipping", "email": "shipping@example.test", "phone": "09127778899"},
        }
        responses = []
        for value in (existing, existing):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(value).encode()
            responses.append(response)
        customer = {"first_name": "Sara", "last_name": "Test", "email": "sara@example.test", "phone": "09125556677"}
        with patch.object(self.client.opener, "open", side_effect=responses) as open_url:
            result = self.client.update_customer(12, customer)

        self.assertEqual(open_url.call_count, 2)
        get_req, put_req = [call.args[0] for call in open_url.call_args_list]
        self.assertEqual(get_req.method, "GET")
        self.assertEqual(put_req.method, "PUT")
        self.assertEqual(put_req.full_url, "https://store.example/wp-json/wc/v3/customers/12")
        payload = json.loads(put_req.data)
        self.assertNotIn("billing", payload)
        self.assertNotIn("shipping", payload)
        self.assertEqual(result["id"], 12)

    def test_customer_profile_update_preserves_distinct_billing_and_shipping_contacts(self):
        existing = {
            "id": 12,
            "first_name": "Profile",
            "last_name": "Old",
            "email": "profile@example.test",
            "meta_data": [{"id": 1, "key": "afrand_profile_phone", "value": "09128888888"}],
            "billing": {
                "first_name": "Bill", "last_name": "Contact",
                "email": "billing@example.test", "phone": "09121111111",
                "address_1": "Billing street",
            },
            "shipping": {
                "first_name": "Ship", "last_name": "Contact",
                "email": "shipping@example.test", "phone": "09123333333",
                "address_1": "Shipping street",
            },
        }
        updated = {
            **existing,
            "first_name": "Profile",
            "last_name": "Updated",
            "email": "new-profile@example.test",
            "meta_data": [{"id": 1, "key": "afrand_profile_phone", "value": "09129999999"}],
        }
        profile = {
            "first_name": "Profile",
            "last_name": "Updated",
            "email": "new-profile@example.test",
            "phone": "09129999999",
        }
        with patch.object(self.client, "request", side_effect=[existing, updated]) as request:
            result = self.client.update_customer(12, profile)

        self.assertEqual(request.call_args_list[0].args[:2], ("GET", "customers/12"))
        update_payload = request.call_args_list[1].kwargs["payload"]
        self.assertNotIn("billing", update_payload)
        self.assertNotIn("shipping", update_payload)
        self.assertEqual(update_payload["first_name"], "Profile")
        self.assertEqual(update_payload["last_name"], "Updated")
        self.assertEqual(update_payload["email"], "new-profile@example.test")
        self.assertEqual(update_payload["meta_data"], [{"id": 1, "key": "afrand_profile_phone", "value": "09129999999"}])
        self.assertEqual(result["id"], 12)

    def test_customer_search_requests_the_requested_page_and_reports_more_results(self):
        response = MagicMock()
        result = [{"id": index, "first_name": "Sara"} for index in range(20)]
        response.__enter__.return_value.read.return_value = json.dumps(result).encode()
        with patch.object(self.client.opener, "open", return_value=response) as open_url:
            customers, has_more = self.client.search_customers("Sara", page=3)

        req = open_url.call_args.args[0]
        query = parse_qs(urlparse(req.full_url).query)
        self.assertEqual(query["page"], ["3"])
        self.assertEqual(query["per_page"], ["20"])
        self.assertEqual(len(customers), 20)
        self.assertTrue(has_more)

    def test_non_list_product_response_is_an_api_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with patch.object(self.client.opener, "open", return_value=response):
            with self.assertRaises(WooError) as raised:
                self.client.get_products()
        self.assertEqual(getattr(raised.exception, "category", None), "api")

    def test_requests_use_explicit_demo_user_agent_and_keep_basic_auth(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"[]"
        with patch.object(self.client.opener, "open", return_value=response) as open_url:
            for method in ("GET", "POST", "PUT"):
                self.client.request(method, "products", params={"per_page": 1}, payload=None if method == "GET" else {})

        self.assertEqual(open_url.call_count, 3)
        for call in open_url.call_args_list:
            req = call.args[0]
            self.assertEqual(req.get_header("User-agent"), "Afrand-WooCommerce-Demo/1.0")
            self.assertEqual(req.get_header("Authorization"), "Basic Y29uc3VtZXIta2V5OmNvbnN1bWVyLXNlY3JldA==")
            self.assertEqual(urlparse(req.full_url).scheme, "https")
            self.assertNotIn("context", call.kwargs)

    def test_customer_orders_request_is_scoped_and_response_is_minimal(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps([{
            "id": 88, "number": "88", "status": "completed", "date_created": "2026-10-05T12:00:00",
            "total": "250000", "currency": "IRR", "billing": {"email": "private@example.test"},
            "line_items": [{"name": "محصول", "quantity": 2, "price": 125000}],
        }]).encode()
        with patch.object(self.client.opener, "open", return_value=response) as open_url:
            orders = self.client.get_customer_orders(7)

        req = open_url.call_args.args[0]
        query = parse_qs(urlparse(req.full_url).query)
        self.assertEqual(query["customer"], ["7"])
        self.assertEqual(query["per_page"], ["50"])
        self.assertEqual(orders[0]["id"], 88)
        self.assertEqual(orders[0]["line_items"], [{"name": "محصول", "quantity": 2}])
        self.assertNotIn("billing", orders[0])


if __name__ == "__main__":
    unittest.main()
