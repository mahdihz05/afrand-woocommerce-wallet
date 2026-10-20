"""Domain rules for safe customer and spreadsheet matching."""

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def positive_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a quantity")
    try:
        number = Decimal(str(value).translate(_DIGITS))
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise ValueError("quantity must be a positive integer")
        return int(number)
    except (InvalidOperation, TypeError):
        raise ValueError("quantity must be a positive integer") from None


def normalize_phone(value: object) -> str:
    """Normalize Persian/Arabic digits and common Iranian mobile prefixes."""
    digits = re.sub(r"\D", "", str(value or "").translate(_DIGITS))
    if digits.startswith("0098") and len(digits) == 14:
        return "0" + digits[4:]
    if digits.startswith("98") and len(digits) == 12:
        return "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("9"):
        return "0" + digits
    return digits


def find_customer_by_phone(phone: object, customers: list[dict]) -> tuple[str, dict | None]:
    """Return ('new'|'existing'|'ambiguous', customer) by exact normalized phone."""
    wanted = normalize_phone(phone)
    if not wanted:
        return "new", None
    matches = [c for c in customers if normalize_phone(c.get("phone")) == wanted]
    if len(matches) == 1:
        return "existing", matches[0]
    if len(matches) > 1:
        return "ambiguous", None
    return "new", None


def validate_import_rows(rows: list[dict], products_by_sku: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Validate a normalized spreadsheet; input row 1 is the header."""
    valid: list[dict] = []
    invalid: list[dict] = []
    by_order: dict[str, list[dict]] = defaultdict(list)
    for row_number, source in enumerate(rows, start=2):
        row = dict(source)
        row["row_number"] = source.get("row_number", row_number)
        row["order_ref"] = str(row.get("order_ref", "")).strip()
        row["customer_name"] = str(row.get("customer_name", "")).strip()
        row["customer_phone"] = str(row.get("customer_phone", "")).strip()
        row["product_sku"] = str(row.get("product_sku", "")).strip()
        row["customer_email"] = str(row.get("customer_email") or "").strip()
        row["errors"] = []
        if not row["order_ref"]:
            row["errors"].append("شماره فاکتور الزامی است")
        if not row["customer_name"]:
            row["errors"].append("نام مشتری الزامی است")
        if len(normalize_phone(row["customer_phone"])) < 10:
            row["errors"].append("شماره تلفن مشتری باید دست‌کم ۱۰ رقم باشد")
        try:
            quantity = positive_integer(row.get("quantity", 0))
            row["quantity"] = quantity
        except (TypeError, ValueError):
            row["errors"].append("تعداد باید عدد صحیح بزرگ‌تر از صفر باشد")
        if row["customer_email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", row["customer_email"]):
            row["errors"].append("ایمیل مشتری معتبر نیست")
        product = products_by_sku.get(row["product_sku"])
        if not product:
            row["errors"].append(f"کد کالا (SKU) در فروشگاه پیدا نشد: {row['product_sku'] or 'خالی'}")
        else:
            row["product"] = product
        if row["errors"]:
            invalid.append(row)
        else:
            valid.append(row)
            by_order[row["order_ref"]].append(row)

    # A single order reference must not silently mix different customers or
    # commit only a subset of its lines when any sibling row is invalid.
    invalid_refs = {r["order_ref"] for r in invalid if r["order_ref"]}
    conflicting_refs = {
        ref for ref, group in by_order.items()
        if len({normalize_phone(r["customer_phone"]) for r in group}) > 1
        or len({r["customer_email"].casefold() for r in group if r["customer_email"]}) > 1
    }
    rejected_refs = invalid_refs | conflicting_refs
    if rejected_refs:
        retained = []
        for row in valid:
            if row["order_ref"] in rejected_refs:
                row["errors"] = ["یکی از ردیف‌های این سفارش نامعتبر است؛ کل سفارش تا اصلاح فایل رد شد"]
                invalid.append(row)
            else:
                retained.append(row)
        valid = retained
        for row in invalid:
            if row["order_ref"] in rejected_refs and not any("کل سفارش" in error for error in row["errors"]):
                row["errors"].append("کل سفارش تا اصلاح فایل رد شد")
    if conflicting_refs:
        for row in invalid:
            if row["order_ref"] in conflicting_refs and "شماره مشتری در ردیف‌های این سفارش یکسان نیست" not in row["errors"]:
                row["errors"].append("شماره مشتری در ردیف‌های این سفارش یکسان نیست")
    return valid, invalid
