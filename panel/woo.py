"""Minimal, server-side WooCommerce REST API client; never logs credentials."""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse


class WooError(Exception):
    def __init__(self, message: str, status: int | None = None, category: str | None = None):
        super().__init__(message)
        self.status = status
        self.category = category


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not send authenticated requests to ANY redirect target.
        raise WooError("تغییر مسیر API ووکامرس مجاز نیست.", category="connection")


class WooCommerceClient:
    def __init__(self, config):
        self.base_url = str(config.get("WC_BASE_URL", "")).rstrip("/")
        self.consumer_key = str(config.get("WC_CONSUMER_KEY", ""))
        self.consumer_secret = str(config.get("WC_CONSUMER_SECRET", ""))
        self.staging_host = str(config.get("WC_STAGING_HOST", "")).lower().strip()
        self.timeout = int(config.get("WC_TIMEOUT", 15))
        self.read_timeout = int(config.get("WC_READ_TIMEOUT", min(self.timeout, 5)))
        self.opener = urllib.request.build_opener(RejectRedirects())

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.consumer_key and self.consumer_secret)

    @property
    def host_matches_staging(self) -> bool:
        parsed = urlparse(self.base_url)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.hostname.lower() == self.staging_host
            and self.staging_host
        )

    def _url(self, path: str, params: dict | None = None) -> str:
        if not self.base_url or not self.host_matches_staging:
            raise WooError("نشانی ووکامرس با میزبان staging تأییدشده منطبق نیست.", category="connection")
        safe_path = path.lstrip("/")
        url = f"{self.base_url}/wp-json/wc/v3/{safe_path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        return url

    def request(self, method: str, path: str, params: dict | None = None, payload: dict | None = None):
        try:
            return self._request_once(method, path, params, payload)
        except WooError as exc:
            if method.upper() != "GET" or exc.category != "connection":
                raise
            # Only safe reads may retry. Never repeat POST/PUT after a timeout.
            return self._request_once(method, path, params, payload)

    def _request_once(self, method: str, path: str, params: dict | None = None, payload: dict | None = None):
        if not self.configured:
            raise WooError("کلید API ووکامرس تنظیم نشده است.", category="connection")
        token = base64.b64encode(f"{self.consumer_key}:{self.consumer_secret}".encode()).decode()
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self._url(path, params),
            data=body,
            method=method.upper(),
            headers={"Authorization": f"Basic {token}", "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Afrand-WooCommerce-Demo/1.0"},
        )
        try:
            with self.opener.open(req, timeout=self.read_timeout if method.upper() == "GET" else self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(1000).decode("utf-8", "replace")
                parsed = json.loads(body)
                message = str(parsed.get("message", "خطای API ووکامرس"))[:240]
            except Exception:
                message = "درخواست API ووکامرس ناموفق بود."
            raise WooError(message, exc.code) from None
        except (TimeoutError, OSError, urllib.error.URLError):
            # A write timeout is an unknown outcome; callers must not retry automatically.
            raise WooError("پاسخ از ووکامرس دریافت نشد؛ وضعیت عملیات نامشخص است.", category="connection") from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WooError("پاسخ ووکامرس JSON معتبر نبود.", category="api") from None

    def check_connection(self) -> bool:
        """Verify the configured credentials can reach the WooCommerce REST API."""
        result = self.request("GET", "products", params={"per_page": 1, "status": "publish"})
        if not isinstance(result, list):
            raise WooError("پاسخ سلامت API ووکامرس معتبر نیست.", category="api")
        return True

    def get_products(self, search: str = "") -> list[dict]:
        params = {"per_page": 50, "status": "publish"}
        if search.strip():
            params["search"] = search.strip()[:100]
        results = self.request("GET", "products", params=params)
        if not isinstance(results, list):
            raise WooError("ساختار پاسخ فهرست محصولات معتبر نیست.", category="api")
        return [
            {"id": p.get("id"), "name": p.get("name", ""), "sku": p.get("sku", ""), "price": p.get("price", ""), "stock_status": p.get("stock_status", "")}
            for p in results if isinstance(p, dict)
        ]

    def get_product(self, product_id: int) -> dict:
        return self.request("GET", f"products/{int(product_id)}")

    def get_products_by_sku(self, sku: str) -> list[dict]:
        results = self.request("GET", "products", params={"sku": sku.strip(), "status": "publish", "per_page": 100})
        if not isinstance(results, list):
            raise WooError("ساختار پاسخ محصول معتبر نیست.", category="api")
        return [p for p in results if isinstance(p, dict) and str(p.get("sku", "")).casefold() == sku.strip().casefold()]

    def search_customers(self, query: str, page: int = 1, per_page: int = 20) -> tuple[list[dict], bool]:
        term = query.strip()[:100]
        if not term:
            return [], False
        if page < 1 or per_page < 1 or per_page > 100:
            raise ValueError("customer search pagination is out of range")
        params = {"per_page": per_page, "page": page}
        params["email" if "@" in term else "search"] = term
        result = self.request("GET", "customers", params=params)
        if not isinstance(result, list):
            raise WooError("ساختار پاسخ فهرست مشتریان معتبر نیست.", category="api")
        customers = [self._public_customer(c) for c in result if isinstance(c, dict)]
        return customers, len(result) == per_page

    def search_customers_by_phone(self, phone: str) -> tuple[list[dict], bool]:
        from .domain import normalize_phone

        wanted = normalize_phone(phone)
        matches = []
        page = 1
        page_size = 100
        max_pages = 20
        while page <= max_pages:
            result = self.request("GET", "customers", params={"per_page": page_size, "page": page})
            if not isinstance(result, list):
                raise WooError("ساختار پاسخ فهرست مشتریان معتبر نیست.", category="api")
            matches.extend(self._public_customer(c) for c in result if isinstance(c, dict) and normalize_phone(self._public_customer(c).get("phone")) == wanted)
            if len(result) < page_size:
                unique = {str(c.get("id")): c for c in matches}
                return list(unique.values()), True
            page += 1
        unique = {str(c.get("id")): c for c in matches}
        return list(unique.values()), False

    @staticmethod
    def _public_customer(customer: dict) -> dict:
        billing = customer.get("billing") or {}
        profile_phone = next(
            (item.get("value") for item in customer.get("meta_data", [])
             if isinstance(item, dict) and item.get("key") == "afrand_profile_phone"),
            "",
        )
        return {
            "id": customer.get("id"),
            "first_name": customer.get("first_name", ""),
            "last_name": customer.get("last_name", ""),
            "email": customer.get("email", ""),
            "phone": profile_phone or customer.get("phone") or billing.get("phone", ""),
        }

    def get_customer(self, customer_id: int) -> dict:
        return self._public_customer(self.request("GET", f"customers/{int(customer_id)}"))

    def get_customer_orders(self, customer_id: int) -> list[dict]:
        results = self.request("GET", "orders", params={"customer": int(customer_id), "per_page": 50, "orderby": "date", "order": "desc"})
        if not isinstance(results, list):
            raise WooError("ساختار پاسخ سفارش‌های مشتری معتبر نیست.", category="api")
        return [
            {
                "id": order.get("id"),
                "number": order.get("number", ""),
                "status": order.get("status", ""),
                "date_created": order.get("date_created", ""),
                "total": order.get("total", ""),
                "currency": order.get("currency", ""),
                "source": "in_person" if any(m.get("key") == "afrand_order_source" and m.get("value") == "in_person" for m in order.get("meta_data", []) if isinstance(m, dict)) else "online",
                "line_items": [
                    {"name": item.get("name", ""), "quantity": item.get("quantity", 0)}
                    for item in order.get("line_items", []) if isinstance(item, dict)
                ],
            }
            for order in results if isinstance(order, dict)
        ]

    @staticmethod
    def _customer_payload(customer: dict) -> dict:
        first = str(customer.get("first_name", "")).strip()
        last = str(customer.get("last_name", "")).strip()
        email = str(customer.get("email", "")).strip()
        phone = str(customer.get("phone", "")).strip()
        return {
            "email": email,
            "first_name": first,
            "last_name": last,
            "billing": {"first_name": first, "last_name": last, "email": email, "phone": phone},
            "shipping": {"first_name": first, "last_name": last},
        }

    def create_customer(self, customer: dict) -> dict:
        return self._public_customer(self.request("POST", "customers", payload=self._customer_payload(customer)))

    def update_customer(self, customer_id: int, customer: dict) -> dict:
        path = f"customers/{int(customer_id)}"
        existing = self.request("GET", path)
        if not isinstance(existing, dict):
            raise WooError("ساختار پاسخ مشتری معتبر نیست.", category="api")
        payload = {
            "email": str(customer.get("email", "")).strip(),
            "first_name": str(customer.get("first_name", "")).strip(),
            "last_name": str(customer.get("last_name", "")).strip(),
        }
        phone_item = {"key": "afrand_profile_phone", "value": str(customer.get("phone", "")).strip()}
        targets = [item for item in existing.get("meta_data", [])
                   if isinstance(item, dict) and item.get("key") == "afrand_profile_phone"]
        if len(targets) > 1 or (targets and not targets[0].get("id")):
            raise WooError("شناسهٔ متادیتای موبایل قطعی نیست؛ بررسی دستی لازم است.", category="api")
        if targets:
            phone_item["id"] = targets[0]["id"]
        # Woo merges metadata by ID/key; omit untouched addresses/metadata.
        payload["meta_data"] = [phone_item]
        result = self.request("PUT", path, payload=payload)
        return self._public_customer(result)

    def create_order(self, order: dict) -> dict:
        return self.request("POST", "orders", payload=order)

    def find_order_by_request_id(self, request_id: str) -> dict | None:
        # Bound the scan; absence never authorizes an uncertain write to be retried.
        for page in range(1, 21):
            orders = self.request("GET", "orders", params={"per_page": 100, "page": page, "orderby": "date", "order": "desc"})
            if not isinstance(orders, list):
                raise WooError("پاسخ بررسی سفارش معتبر نیست.", category="api")
            matches = [o for o in orders if any(m.get("key") == "afrand_demo_request_id" and m.get("value") == request_id for m in o.get("meta_data", []) if isinstance(m, dict))]
            if len(matches) > 1:
                raise WooError("چند سفارش با یک شناسه درخواست پیدا شد؛ بررسی دستی لازم است.", category="api")
            if matches:
                return matches[0]
            if len(orders) < 100:
                return None
        return None
