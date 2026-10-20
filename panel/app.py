"""Flask app for the local Afrand WooCommerce demo."""

import hashlib
import io
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from .domain import find_customer_by_phone, normalize_phone, positive_integer, validate_import_rows
from .accounting import FIELDS, read_accounting_export
from .ledger import Ledger
from .woo import WooCommerceClient, WooError
from .auth import configure_login

ALLOWED_ORDER_STATUSES = {"pending", "on-hold"}


def _as_bool(value) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def create_app(test_config: dict | None = None, woo_client=None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.update(
        SECRET_KEY="local-demo-change-me",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        DATABASE=str(Path(app.instance_path) / "panel.sqlite3"),
        WC_BASE_URL="",
        WC_STAGING_HOST="",
        WC_CONSUMER_KEY="",
        WC_CONSUMER_SECRET="",
        WC_WRITES_ENABLED=False,
        WC_ENVIRONMENT="staging",
        PANEL_AUTH_ENABLED=False,
        PANEL_USERNAME="demo",
        PANEL_PASSWORD_HASH="",
        WALLET_MODE=False,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    ledger = Ledger(app.config["DATABASE"])
    woo = woo_client or WooCommerceClient(app.config)
    app.extensions["ledger"] = ledger
    app.extensions["woo"] = woo

    @app.errorhandler(413)
    def upload_too_large(error):
        return jsonify(error="حجم فایل بیشتر از ۲ مگابایت است.", category="validation"), 413

    @app.before_request
    def reject_cross_site_writes():
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("Origin")
            if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and origin.rstrip("/") != request.host_url.rstrip("/")):
                return jsonify(error="درخواست باید از خود پنل ارسال شود.", category="forbidden"), 403

    configure_login(app)

    if _as_bool(app.config.get("WALLET_MODE")):
        from .wallet import configure_wallet
        configure_wallet(app, woo, ledger)
        return app

    def store_host_matches() -> bool:
        parsed = urlparse(str(app.config.get("WC_BASE_URL", "")))
        expected = str(app.config.get("WC_STAGING_HOST", "")).lower().strip()
        return bool(parsed.scheme == "https" and parsed.hostname and expected and parsed.hostname.lower() == expected)

    def integration_configured() -> bool:
        return bool(app.config.get("WC_BASE_URL") and app.config.get("WC_CONSUMER_KEY") and app.config.get("WC_CONSUMER_SECRET"))

    def writes_enabled() -> bool:
        return bool(
            _as_bool(app.config.get("WC_WRITES_ENABLED"))
            and str(app.config.get("WC_ENVIRONMENT", "")).lower() == "staging"
            and store_host_matches()
            and integration_configured()
        )

    def api_error(category: str, message: str, status: int, **details):
        return jsonify(error=message, category=category, **details), status

    def classify_woo_error(exc: WooError) -> tuple[str, str, int]:
        if exc.status == 401:
            return "unauthorized", "احراز هویت ووکامرس ناموفق بود؛ کلید API را بررسی کن.", 401
        if exc.status == 403:
            return "forbidden", "کلید API اجازهٔ این عملیات را ندارد.", 403
        if exc.category == "api":
            return "api", "پاسخ API ووکامرس معتبر نبود.", exc.status or 502
        if exc.category == "connection" or exc.status is None:
            return "connection", "پاسخی از ووکامرس دریافت نشد؛ وضعیت عملیات را پیش از تلاش دوباره بررسی کن.", 502
        return "api", "درخواست ووکامرس ناموفق بود؛ جزئیات API برای جلوگیری از افشای اطلاعات پنهان شده است.", exc.status

    def woo_error_response(exc: WooError, **details):
        category, message, status = classify_woo_error(exc)
        return api_error(category, message, status, **details)

    def require_read_connection():
        if not integration_configured():
            return api_error("connection", "کلید API ووکامرس در بک‌اند تنظیم نشده است.", 503)
        if not store_host_matches():
            return api_error("connection", "نشانی ووکامرس با میزبان تأییدشده منطبق نیست.", 503)
        return None

    def require_write_connection():
        if not writes_enabled():
            return api_error("forbidden", "ثبت در ووکامرس غیرفعال است؛ محیط staging و مجوز نوشتن باید صریحاً فعال باشند.", 403)
        return None

    def normalize_customer_fields(payload: dict) -> tuple[dict | None, str | None]:
        first = str(payload.get("first_name", "")).strip()
        last = str(payload.get("last_name", "")).strip()
        email = str(payload.get("email", "")).strip()
        phone = str(payload.get("phone", "")).strip()
        if not first or not phone or not email:
            return None, "نام، موبایل و ایمیل مشتری لازم است."
        if len(normalize_phone(phone)) < 10:
            return None, "شمارهٔ موبایل معتبر نیست."
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            return None, "ایمیل مشتری معتبر نیست."
        return {"first_name": first, "last_name": last, "phone": phone, "email": email}, None

    def customer_duplicates(customer: dict, exclude_id=None):
        found = []
        email = customer["email"].strip().casefold()
        email_matches, email_has_more = woo.search_customers(email, per_page=50)
        if email_has_more:
            return [], False
        found.extend(
            candidate for candidate in email_matches
            if str(candidate.get("email", "")).strip().casefold() == email
        )
        phone_matches, complete = woo.search_customers_by_phone(customer["phone"])
        if not complete:
            return [], False
        wanted_phone = normalize_phone(customer["phone"])
        found.extend(
            candidate for candidate in phone_matches
            if normalize_phone(candidate.get("phone")) == wanted_phone
        )
        unique = {}
        for index, candidate in enumerate(found):
            candidate_id = candidate.get("id")
            key = str(candidate_id) if candidate_id is not None else f"missing-id-{index}"
            if exclude_id is None or candidate_id is None or str(candidate_id) != str(exclude_id):
                unique[key] = candidate
        return list(unique.values()), True

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        configured = integration_configured() and store_host_matches()
        connected = False
        api_error_text = None
        api_error_category = None
        if integration_configured() and not store_host_matches():
            api_error_category = "connection"
            api_error_text = "نشانی ووکامرس با میزبان تأییدشده منطبق نیست."
        if configured:
            try:
                connected = bool(woo.check_connection())
            except WooError as exc:
                connected = False
                api_error_category, api_error_text, _ = classify_woo_error(exc)
            except Exception:
                connected = False
                api_error_category = "connection"
                api_error_text = "ارتباط REST API ووکامرس تأیید نشد."
        return jsonify(
            connected=connected,
            writes_enabled=writes_enabled() and connected,
            environment="staging" if str(app.config.get("WC_ENVIRONMENT", "")).lower() == "staging" else "read-only",
            store_host=urlparse(str(app.config.get("WC_BASE_URL", ""))).hostname or "",
            credentials_present=integration_configured(),
            **({} if connected or not integration_configured() else {"api_error": api_error_text or "ارتباط REST API ووکامرس تأیید نشد.", "api_error_category": api_error_category or "connection"}),
        )

    @app.get("/api/products")
    def products():
        blocked = require_read_connection()
        if blocked:
            return blocked
        try:
            term = request.args.get("search", "").strip()
            results = woo.get_products(term)
            if term:
                by_id = {p["id"]: p for p in results}
                for product in woo.get_products_by_sku(term):
                    by_id[product["id"]] = product
                results = list(by_id.values())
            return jsonify(products=results)
        except WooError as exc:
            return woo_error_response(exc)
        except Exception:
            return api_error("api", "دریافت محصولات از ووکامرس ناموفق بود.", 502)

    @app.get("/api/customers")
    def customers():
        blocked = require_read_connection()
        if blocked:
            return blocked
        query = request.args.get("q", "").strip()
        try:
            page = int(request.args.get("page", "1"))
        except ValueError:
            return api_error("validation", "شمارهٔ صفحه معتبر نیست.", 400)
        page_size = 20
        if page < 1:
            return api_error("validation", "شمارهٔ صفحه باید مثبت باشد.", 400)
        if len(query) < 2:
            return jsonify(customers=[], page=page, per_page=page_size, has_more=False)
        try:
            if len(normalize_phone(query)) >= 10 and "@" not in query:
                all_results, complete = woo.search_customers_by_phone(query)
                if not complete:
                    return api_error("api", "فهرست مشتریان کامل بررسی نشد؛ برای جلوگیری از مشتری تکراری ساخت مشتری متوقف شد.", 409)
                start = (page - 1) * page_size
                results = all_results[start:start + page_size]
                has_more = len(all_results) > start + page_size
            else:
                results, has_more = woo.search_customers(query, page=page, per_page=page_size)
            return jsonify(customers=results, page=page, per_page=page_size, has_more=has_more)
        except WooError as exc:
            return woo_error_response(exc)
        except Exception:
            return api_error("api", "جست‌وجوی مشتریان از ووکامرس ناموفق بود.", 502)

    @app.get("/api/customers/<int:customer_id>")
    def customer_detail(customer_id: int):
        blocked = require_read_connection()
        if blocked:
            return blocked
        try:
            return jsonify(customer=woo.get_customer(customer_id))
        except WooError as exc:
            return woo_error_response(exc)
        except Exception:
            return api_error("api", "دریافت اطلاعات مشتری از ووکامرس ناموفق بود.", 502)

    @app.get("/api/customers/<int:customer_id>/orders")
    def customer_orders(customer_id: int):
        blocked = require_read_connection()
        if blocked:
            return blocked
        try:
            return jsonify(orders=woo.get_customer_orders(customer_id))
        except WooError as exc:
            return woo_error_response(exc)
        except Exception:
            return api_error("api", "دریافت سفارش‌های مشتری از ووکامرس ناموفق بود.", 502)

    @app.post("/api/customers")
    def create_customer():
        blocked = require_write_connection()
        if blocked:
            return blocked
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return api_error("validation", "اطلاعات مشتری معتبر نیست.", 400)
        if payload.get("confirm") is not True:
            return api_error("validation", "برای ایجاد مشتری باید تأیید نهایی انجام شود.", 400)
        customer, issue = normalize_customer_fields(payload)
        if issue:
            return api_error("validation", issue, 400)
        customer_key = "customer-" + normalize_phone(customer["phone"])

        def replay_or_reconcile(state: str, saved: dict | None = None):
            if state == "succeeded" and saved and saved.get("id"):
                return jsonify(customer=saved, replayed=True), 200
            try:
                matches, complete = woo.search_customers_by_phone(customer["phone"])
            except WooError as exc:
                return woo_error_response(exc, state=state)
            except Exception:
                return api_error("api", "بررسی وضعیت ایجاد مشتری ناموفق بود؛ عملیات دوباره اجرا نشد.", 409, state=state)
            if complete and len(matches) == 1 and matches[0].get("id"):
                saved = matches[0]
                ledger.finish(customer_key, "succeeded", saved)
                return jsonify(customer=saved, replayed=True), 200
            return api_error("validation", "نتیجهٔ ایجاد مشتری قطعی نیست؛ پیش از تلاش دوباره بررسی دستی لازم است.", 409, state=state)

        try:
            prior_state, prior = ledger.lookup(customer_key)
            if prior_state:
                return replay_or_reconcile(prior_state, prior)
            duplicates, complete = customer_duplicates(customer)
            if not complete:
                prior_state, prior = ledger.lookup(customer_key)
                if prior_state:
                    return replay_or_reconcile(prior_state, prior)
                return api_error("api", "بررسی کامل شماره‌های مشتریان ممکن نشد؛ برای جلوگیری از ثبت تکراری عملیات متوقف شد.", 409)
            if duplicates:
                prior_state, prior = ledger.lookup(customer_key)
                if prior_state:
                    return replay_or_reconcile(prior_state, prior)
                return api_error("validation", "مشتری با این ایمیل یا موبایل وجود دارد؛ ابتدا رکورد موجود را جست‌وجو و انتخاب کن.", 409)
            state, saved, prior_state = ledger.claim(customer_key, "customer")
            if state == "replay":
                return jsonify(customer=saved, replayed=True), 200
            if state == "blocked":
                return replay_or_reconcile(prior_state or "unknown", saved)
            created = woo.create_customer(customer)
            if not isinstance(created, dict) or not created.get("id"):
                raise WooError("پاسخ ایجاد مشتری شناسه نداشت.", category="api")
            ledger.finish(customer_key, "succeeded", created)
            return jsonify(customer=created, replayed=False), 201
        except WooError as exc:
            ledger.finish(customer_key, "unknown")
            category, message, status = classify_woo_error(exc)
            return api_error(category, message, status, state="unknown")
        except Exception:
            ledger.finish(customer_key, "unknown")
            return api_error("connection", "نتیجهٔ ایجاد مشتری نامشخص است؛ پیش از تلاش دوباره وضعیت را بررسی کن.", 502, state="unknown")

    @app.put("/api/customers/<int:customer_id>")
    def update_customer(customer_id: int):
        blocked = require_write_connection()
        if blocked:
            return blocked
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return api_error("validation", "اطلاعات مشتری معتبر نیست.", 400)
        if payload.get("confirm") is not True:
            return api_error("validation", "برای به‌روزرسانی مشتری باید تأیید نهایی انجام شود.", 400)
        try:
            existing = woo.get_customer(customer_id)
            merged = {**existing, **payload}
            customer, issue = normalize_customer_fields(merged)
            if issue:
                return api_error("validation", issue, 400)
            duplicates, complete = customer_duplicates(customer, exclude_id=customer_id)
            if not complete:
                return api_error("api", "بررسی کامل شماره‌های مشتریان ممکن نشد؛ برای جلوگیری از ثبت تکراری عملیات متوقف شد.", 409)
            if duplicates:
                return api_error("validation", "ایمیل یا موبایل به مشتری دیگری تعلق دارد؛ تغییری ثبت نشد.", 409)
            return jsonify(customer=woo.update_customer(customer_id, customer))
        except WooError as exc:
            return woo_error_response(exc)
        except Exception:
            return api_error("api", "به‌روزرسانی مشتری در ووکامرس ناموفق بود.", 502)

    @app.get("/template.xlsx")
    def spreadsheet_template():
        sample = Path(app.root_path).parent / "demo" / "afrand-accounting-sample.xlsx"
        if not sample.exists():
            return api_error("validation", "فایل نمونه حسابداری در دسترس نیست.", 404)
        return send_file(sample, as_attachment=True, download_name="afrand-accounting-sample.xlsx")

    @app.get("/demo.xlsx")
    def demo_spreadsheet():
        sample = Path(app.root_path).parent / "demo" / "afrand-demo.xlsx"
        if not sample.exists():
            return api_error("validation", "فایل دموی آماده هنوز ساخته نشده است.", 404)
        return send_file(sample, as_attachment=True, download_name="afrand-demo.xlsx")

    @app.post("/api/import/preview")
    def import_preview():
        blocked = require_read_connection()
        if blocked:
            return blocked
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return api_error("validation", "فایل اکسل را انتخاب کن.", 400)
        filename = secure_filename(upload.filename)
        if not filename.lower().endswith(".xlsx"):
            return api_error("validation", "نسخهٔ دمو فعلاً فقط فایل .xlsx می‌پذیرد.", 400)
        try:
            mapping = json.loads(request.form["mapping"]) if "mapping" in request.form else None
            header_row = int(request.form["header_row"]) if "header_row" in request.form else None
            parsed = read_accounting_export(upload.stream, mapping, header_row)
            if parsed["mapping_required"]:
                return jsonify(parsed)
            rows = parsed.pop("rows")
        except ValueError as exc:
            return api_error("validation", str(exc), 400)
        except Exception:
            return api_error("validation", "فایل اکسل قابل خواندن نیست.", 400)

        skus = sorted({str(row.get("product_sku") or "").strip() for row in rows if row.get("product_sku")})
        products_by_sku = {}
        try:
            for sku in skus:
                for product in woo.get_products_by_sku(sku):
                    if str(product.get("sku", "")).casefold() == sku.casefold():
                        products_by_sku[sku] = product
            valid, invalid = validate_import_rows(rows, products_by_sku)
        except WooError as exc:
            return woo_error_response(exc)
        except Exception:
            return api_error("api", "خواندن فهرست محصولات ووکامرس ناموفق بود.", 502)

        # New WooCommerce customers require an email. We verify existing mobile matches now,
        # before any write; missing email invalidates only rows that would create a customer.
        grouped = defaultdict(list)
        for row in valid:
            grouped[row["order_ref"]].append(row)
        for order_ref, group in grouped.items():
            try:
                matches, complete = woo.search_customers_by_phone(group[0]["customer_phone"])
            except WooError as exc:
                return woo_error_response(exc)
            except Exception:
                return api_error("api", "تطبیق مشتری برای فایل اکسل ناموفق بود.", 502)
            if not complete:
                for row in group:
                    row["errors"] = ["بررسی همهٔ مشتریان کامل نشد؛ برای جلوگیری از تکرار این سفارش متوقف شد"]
                    invalid.append(row)
                valid = [r for r in valid if r["order_ref"] != order_ref]
            elif len(matches) > 1:
                for row in group:
                    row["errors"] = ["چند مشتری با این شماره پیدا شد؛ انتخاب دستی لازم است"]
                    invalid.append(row)
                valid = [r for r in valid if r["order_ref"] != order_ref]
            elif not matches and not str(group[0].get("customer_email") or "").strip():
                for row in group:
                    row["errors"] = ["برای ایجاد مشتری جدید، ایمیل لازم است"]
                    invalid.append(row)
                valid = [r for r in valid if r["order_ref"] != order_ref]

        draft_id = str(uuid.uuid4())
        ledger.save_draft(draft_id, valid)
        public_valid = [{k: v for k, v in row.items() if k != "product"} for row in valid]
        public_invalid = [{k: v for k, v in row.items() if k != "product"} for row in invalid]
        return jsonify(**parsed, draft_id=draft_id, valid_rows=public_valid, invalid_rows=public_invalid, valid_count=len(valid), invalid_count=len(invalid), order_count=len({r["order_ref"] for r in valid}))

    def validate_order_payload(payload: dict):
        if payload.get("confirm") is not True:
            return "برای ثبت باید تأیید نهایی انجام شود."
        request_id = str(payload.get("request_id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
            return "شناسهٔ درخواست معتبر نیست."
        if payload.get("status") not in ALLOWED_ORDER_STATUSES:
            return "وضعیت سفارش را صریحاً انتخاب کن."
        customer = payload.get("customer")
        if not isinstance(customer, dict):
            return "اطلاعات مشتری معتبر نیست."
        if len(normalize_phone(customer.get("phone"))) < 10:
            return "شمارهٔ موبایل مشتری معتبر نیست؛ دست‌کم ۱۰ رقم لازم است."
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return "حداقل یک محصول لازم است."
        for item in items:
            try:
                positive_integer(item.get("product_id", 0))
                positive_integer(item.get("quantity", 0))
            except (TypeError, ValueError, AttributeError):
                return "محصول و تعداد باید معتبر باشند."
        return None

    def process_order(request_id: str, customer_data: dict, items: list[dict], status_value: str, order_ref: str | None = None, accounting_rows=None):
        line_items = []
        for item in items:
            product = woo.get_product(int(item["product_id"]))
            if str(product.get("status", "publish")) != "publish":
                raise ValueError("محصول انتخاب‌شده منتشرشده نیست.")
            if product.get("type", "simple") != "simple":
                raise ValueError("در این دمو فقط محصول ساده قابل ثبت است؛ محصول متغیر نیاز به انتخاب تنوع دارد.")
            if product.get("stock_status") == "outofstock":
                raise ValueError("محصول انتخاب‌شده موجود نیست.")
            line_items.append({"product_id": int(product["id"]), "quantity": positive_integer(item["quantity"])})
        phone = normalize_phone(customer_data.get("phone"))
        if len(phone) < 10:
            raise ValueError("شمارهٔ موبایل مشتری معتبر نیست؛ دست‌کم ۱۰ رقم لازم است.")
        matches, complete = woo.search_customers_by_phone(phone)
        if not complete:
            raise ValueError("بررسی همهٔ مشتریان کامل نشد؛ ساخت مشتری برای جلوگیری از تکرار متوقف شد.")
        match_state, matched = find_customer_by_phone(phone, matches)
        if match_state == "ambiguous":
            raise ValueError("چند مشتری با این شماره پیدا شد؛ سفارش بدون انتخاب دستی ثبت نمی‌شود.")
        email = str(customer_data.get("email", "")).strip()
        if email:
            email_candidates, email_has_more = woo.search_customers(email, per_page=50)
            if email_has_more:
                raise ValueError("بررسی کامل ایمیل مشتری ممکن نشد؛ ساخت مشتری برای جلوگیری از تکرار متوقف شد.")
            email_matches = [
                candidate for candidate in email_candidates
                if str(candidate.get("email", "")).strip().casefold() == email.casefold()
            ]
            allowed_id = matched.get("id") if match_state == "existing" and matched else None
            if any(candidate.get("id") is None or str(candidate.get("id")) != str(allowed_id) for candidate in email_matches):
                raise ValueError("این ایمیل به مشتری دیگری تعلق دارد؛ ابتدا همان رکورد را جست‌وجو و انتخاب کن.")
        if match_state == "existing":
            customer = matched
            if customer_data.get("id") and str(customer_data["id"]) != str(customer.get("id")):
                raise ValueError("اطلاعات مشتری از زمان انتخاب تغییر کرده است؛ دوباره جست‌وجو کن.")
        else:
            if not str(customer_data.get("first_name") or "").strip():
                raise ValueError("نام مشتری جدید لازم است.")
            if not email or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise ValueError("برای ساخت حساب مشتری جدید، ایمیل معتبر لازم است.")
            customer_key = "customer-" + phone
            state, saved, prior_state = ledger.claim(customer_key, "customer")
            if state == "replay" and saved and saved.get("id"):
                customer = saved
            elif state == "blocked":
                # Reconcile after a prior uncertain customer-create request before allowing another.
                matches, complete = woo.search_customers_by_phone(phone)
                if complete and len(matches) == 1:
                    customer = matches[0]
                    ledger.finish(customer_key, "succeeded", customer)
                else:
                    raise ValueError("نتیجهٔ ساخت قبلی مشتری نامشخص است؛ ابتدا در ووکامرس بررسی دستی لازم است.")
            else:
                try:
                    customer = woo.create_customer(customer_data)
                    if not customer.get("id"):
                        raise WooError("پاسخ ایجاد مشتری شناسه نداشت.")
                    ledger.finish(customer_key, "succeeded", customer)
                except Exception:
                    ledger.finish(customer_key, "unknown")
                    raise

        order = {
            "customer_id": int(customer["id"]),
            "status": status_value,
            "set_paid": False,
            "payment_method": "",
            "payment_method_title": "",
            "line_items": line_items,
            "meta_data": [
                {"key": "afrand_order_source", "value": "in_person"},
                {"key": "afrand_demo_request_id", "value": request_id},
            ],
        }
        if order_ref:
            order["meta_data"].append({"key": "afrand_import_reference", "value": order_ref})
        if accounting_rows:
            # Reference information only. Store prices/taxes still govern the Woo order.
            keys = {key for key, _, _, _ in FIELDS} - {"customer_email", "customer_phone", "customer_name"}
            order["meta_data"].append({"key": "afrand_accounting_export", "value": [{key: row[key] for key in keys if key in row} for row in accounting_rows]})
        try:
            result = woo.create_order(order)
        except ValueError:
            raise WooError("نتیجهٔ ثبت سفارش قطعی نیست.", category="api") from None
        if not isinstance(result, dict) or not result.get("id"):
            raise WooError("پاسخ ایجاد سفارش شناسه نداشت؛ وضعیت عملیات نامشخص است.")
        return {"order_id": result["id"], "customer_id": customer["id"], "status": status_value, "order_ref": order_ref}

    @app.post("/api/orders")
    def create_order():
        blocked = require_write_connection()
        if blocked:
            return blocked
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return api_error("validation", "اطلاعات سفارش معتبر نیست.", 400)
        issue = validate_order_payload(payload)
        if issue:
            return api_error("validation", issue, 400)
        request_id = payload["request_id"].strip()
        state, prior, prior_state = ledger.claim(request_id, "manual_order")
        if state == "replay":
            return jsonify(**prior, replayed=True), 200
        if state == "blocked":
            try:
                existing = woo.find_order_by_request_id(request_id)
                if existing:
                    result = {"order_id": existing["id"], "customer_id": existing["customer_id"], "status": existing["status"], "order_ref": None}
                    ledger.finish(request_id, "succeeded", result)
                    return jsonify(**result, replayed=True), 200
            except WooError as exc:
                return woo_error_response(exc, state="unknown")
            return api_error("validation", "این درخواست قبلاً شروع شده ولی نتیجه‌اش قطعی نیست؛ پیش از هر تلاش دوباره ووکامرس را بررسی کن.", 409, state=prior_state)
        try:
            result = process_order(request_id, payload["customer"], payload["items"], payload["status"])
            ledger.finish(request_id, "succeeded", result)
            return jsonify(**result, replayed=False), 201
        except ValueError as exc:
            ledger.finish(request_id, "rejected")
            return api_error("validation", str(exc), 409, state="rejected")
        except WooError as exc:
            ledger.finish(request_id, "unknown")
            category, message, status = classify_woo_error(exc)
            return api_error(category, message, status, state="unknown")
        except Exception:
            ledger.finish(request_id, "unknown")
            return api_error("connection", "خطای غیرمنتظره؛ وضعیت سفارش را پیش از تلاش دوباره بررسی کن.", 502, state="unknown")

    @app.post("/api/import/commit")
    def import_commit():
        blocked = require_write_connection()
        if blocked:
            return blocked
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return api_error("validation", "اطلاعات ثبت فایل معتبر نیست.", 400)
        draft_id = str(payload.get("draft_id", ""))
        if payload.get("confirm") is not True:
            return api_error("validation", "برای ثبت، تأیید نهایی لازم است.", 400)
        if payload.get("status") not in ALLOWED_ORDER_STATUSES:
            return api_error("validation", "وضعیت سفارش را صریحاً انتخاب کن.", 400)
        rows = ledger.get_draft(draft_id)
        if not rows:
            return api_error("validation", "پیش‌نمایش منقضی یا نامعتبر است؛ فایل را دوباره بارگذاری کن.", 404)
        groups = defaultdict(list)
        for row in rows:
            groups[row["order_ref"]].append(row)
        results = []
        for order_ref, group in groups.items():
            if len(normalize_phone(group[0].get("customer_phone"))) < 10:
                results.append({"order_ref": order_ref, "state": "needs_review", "category": "validation", "error": "شمارهٔ موبایل مشتری معتبر نیست؛ دست‌کم ۱۰ رقم لازم است."})
                continue
            op_key = "import-" + hashlib.sha256(order_ref.encode("utf-8")).hexdigest()
            state, previous, prior_state = ledger.claim(op_key, "import_order")
            if state == "replay":
                results.append({**previous, "replayed": True})
                continue
            if state == "blocked":
                try:
                    existing = woo.find_order_by_request_id(op_key)
                    if existing:
                        recovered = {"order_id": existing["id"], "customer_id": existing["customer_id"], "status": existing["status"], "order_ref": order_ref}
                        ledger.finish(op_key, "succeeded", recovered)
                        results.append({**recovered, "replayed": True})
                        continue
                except WooError:
                    pass
                results.append({"order_ref": order_ref, "state": "needs_review", "category": "validation", "error": "نتیجهٔ اجرای قبلی قطعی نیست؛ بررسی دستی لازم است."})
                continue
            customer = {
                "first_name": group[0]["customer_name"].split(" ", 1)[0],
                "last_name": group[0]["customer_name"].split(" ", 1)[1] if " " in group[0]["customer_name"] else "",
                "phone": group[0]["customer_phone"],
                "email": group[0].get("customer_email", ""),
            }
            items = [{"product_id": row["product"]["id"], "quantity": row["quantity"]} for row in group]
            try:
                result = process_order(op_key, customer, items, payload["status"], order_ref, accounting_rows=group)
                ledger.finish(op_key, "succeeded", result)
                results.append({**result, "state": "created"})
            except Exception as exc:
                ledger.finish(op_key, "unknown")
                if isinstance(exc, WooError):
                    category, message, _ = classify_woo_error(exc)
                elif isinstance(exc, ValueError):
                    category, message = "validation", str(exc)[:220]
                else:
                    category, message = "api", "خطای غیرمنتظره؛ بررسی دستی وضعیت سفارش لازم است."
                results.append({"order_ref": order_ref, "state": "needs_review", "category": category, "error": message})
        return jsonify(results=results, created=sum(r.get("state") == "created" for r in results), needs_review=sum(r.get("state") == "needs_review" for r in results), replayed=sum(bool(r.get("replayed")) for r in results)), 200

    return app
