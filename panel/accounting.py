"""Normalize a sales-invoice export without changing its financial meaning."""

import re
from datetime import date, datetime
from zipfile import ZipFile

from defusedxml.ElementTree import iterparse
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string


def validate_workbook_resources(stream):
    """Bound compressed OOXML work before openpyxl allocates shared data.

    Reject the whole file rather than silently truncating distant customers.
    Worksheet dimensions are untrusted: actual row/cell coordinates are checked.
    """
    try:
        with ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > 128 or sum(item.file_size for item in entries) > 12 * 1024 * 1024:
                raise ValueError('ساختار فایل اکسل بیش از حد بزرگ است؛ فایل را به بخش‌های کوچک‌تر تقسیم کنید.')
            if len({item.filename for item in entries}) != len(entries):
                raise ValueError('ساختار فایل اکسل معتبر نیست؛ فایل را دوباره از اکسل ذخیره کنید.')
            for item in entries:
                if not (item.filename.startswith('xl/worksheets/') and item.filename.endswith('.xml')):
                    continue
                previous = 0
                with archive.open(item) as source:
                    for event, element in iterparse(source, events=('end',)):
                        tag = element.tag.rsplit('}', 1)[-1]
                        if tag == 'c' and element.get('r'):
                            column, row = coordinate_from_string(element.get('r'))
                            if row > 10000 or column_index_from_string(column) > 100:
                                raise ValueError('فایل باید حداکثر ۱۰۰ ستون و ۱۰۰۰۰ ردیف فیزیکی داشته باشد؛ ردیف‌های دور و خالی را حذف کنید.')
                        if tag == 'row':
                            number = int(element.get('r', previous + 1))
                            if not previous < number <= 10000:
                                raise ValueError('شماره یا تعداد ردیف‌های فایل اکسل معتبر نیست؛ فایل را دوباره ذخیره کنید.')
                            previous = number
                        element.clear()
    finally:
        stream.seek(0)

FIELDS = [
    ("order_ref", "شماره فاکتور", True, ["شماره سند", "شماره سفارش", "invoice no", "invoice number"]),
    ("invoice_date", "تاریخ فاکتور", False, ["تاریخ", "تاریخ سند", "invoice date"]),
    ("customer_code", "کد مشتری", False, ["کد طرف حساب", "customer code"]),
    ("customer_name", "نام مشتری", True, ["طرف حساب", "نام طرف حساب", "نام خریدار", "customer name"]),
    ("customer_phone", "موبایل", True, ["شماره موبایل", "تلفن همراه", "تلفن", "شماره تماس", "customer phone"]),
    ("customer_email", "ایمیل", False, ["پست الکترونیک", "customer email"]),
    ("product_sku", "کد کالا", True, ["کد محصول", "شناسه کالا", "sku", "product sku"]),
    ("product_name", "شرح کالا", False, ["نام کالا", "نام محصول", "شرح محصول", "product name"]),
    ("unit", "واحد", False, ["واحد کالا"]),
    ("quantity", "تعداد", True, ["مقدار", "quantity", "qty"]),
    ("unit_price", "قیمت واحد", False, ["فی", "نرخ", "مبلغ واحد", "unit price"]),
    ("discount", "تخفیف", False, ["مبلغ تخفیف"]),
    ("tax", "مالیات", False, ["مالیات و عوارض", "مبلغ مالیات"]),
    ("line_total", "مبلغ ردیف", False, ["مبلغ کل", "مبلغ", "جمع ردیف", "line total"]),
    ("currency", "واحد پول", False, ["currency"]),
]


def normalize_header(value):
    text = str(value or "").casefold().replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"[\s_\-\u200c:()/]+", "", text)


def suggest_mapping(headers, fields=None):
    result = {}
    for key, label, _, aliases in fields or FIELDS:
        names = {normalize_header(name) for name in [key, label, *aliases]}
        matches = [index for index, header in enumerate(headers) if normalize_header(header) in names]
        # Repeated names need an explicit choice rather than silently taking one.
        if len(matches) == 1:
            result[key] = matches[0]
    return result


def cell_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class MultipleSheetsError(ValueError):
    """A single-sheet import must not silently discard other worksheets."""


def read_accounting_export(stream, mapping=None, header_row=None, fields=None, max_rows=500, positional_fields=None, single_sheet=False):
    field_definitions = fields or FIELDS
    validate_workbook_resources(stream)
    workbook = load_workbook(stream, read_only=True, data_only=True)
    try:
        if single_sheet and len(workbook.worksheets) != 1:
            raise MultipleSheetsError('فایل چند برگه دارد؛ برای جلوگیری از جا افتادن مشتریان، همهٔ ردیف‌ها را در یک برگه جمع کنید و فایل تک‌برگه‌ای بارگذاری کنید.')
        # A forged/small dimension must never hide real records later in XML.
        workbook.active.reset_dimensions()
        iterator = workbook.active.iter_rows(values_only=True)
        leading = []
        for _ in range(15):
            cells = next(iterator, None)
            if cells is None:
                break
            leading.append(cells)
        if not leading:
            raise ValueError("فایل خالی است.")
        no_header = positional_fields is not None and (header_row == 0 or (
            header_row is None and len(leading[0]) >= 2
            and re.fullmatch(r'0?9\d{9}', str(leading[0][1] or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')).strip())
            and not any(len(suggest_mapping(row, field_definitions)) >= 2 for row in leading)))
        if no_header:
            index = -1
        elif header_row is None:
            index = max(range(len(leading)), key=lambda i: len(suggest_mapping(leading[i], field_definitions)))
        else:
            if isinstance(header_row, bool) or not isinstance(header_row, int) or not 1 <= header_row <= len(leading):
                raise ValueError("ردیف عنوان ستون‌ها معتبر نیست؛ یکی از ۱۵ ردیف اول را انتخاب کنید.")
            index = header_row - 1
        headers = list(positional_fields) if no_header else [str(value or "").strip() for value in leading[index]]
        detected = ({key: i for i, key in enumerate(positional_fields)} if no_header else suggest_mapping(headers, field_definitions)) if mapping is None else mapping
        if not isinstance(detected, dict):
            raise ValueError("انتخاب ستون‌ها معتبر نیست.")
        fields = {field[0] for field in field_definitions}
        for key, col in detected.items():
            if key not in fields or isinstance(col, bool) or not isinstance(col, int) or not 0 <= col < len(headers):
                raise ValueError("انتخاب ستون‌ها معتبر نیست.")
        if len(set(detected.values())) != len(detected):
            raise ValueError("هر ستون فایل را فقط به یک فیلد اختصاص دهید.")
        details = {
            "headers": headers, "mapping": detected, "header_row": index + 1,
            "fields": [{"key": key, "label": label, "required": required} for key, label, required, _ in field_definitions],
            "header_candidates": [{"row": i + 1, "values": [str(v or "") for v in row]} for i, row in enumerate(leading)],
        }
        if any(required and key not in detected for key, _, required, _ in field_definitions):
            return {**details, "mapping_required": True}
        rows = []

        def append_row(number, cells):
            if all(value in (None, "") for value in cells):
                return
            row = {key: cell_value(cells[col] if col < len(cells) else None) for key, col in detected.items()}
            row["row_number"] = number
            rows.append(row)
            if len(rows) > max_rows:
                limit = str(max_rows).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
                raise ValueError(f"حداکثر {limit} ردیف در هر فایل مجاز است.")

        for i, cells in enumerate(leading[index + 1:], start=index + 2):
            append_row(i, cells)
        for i, cells in enumerate(iterator, start=len(leading) + 1):
            append_row(i, cells)
        if not rows:
            raise ValueError("فایل هیچ ردیف فاکتوری ندارد.")
        return {**details, "mapping_required": False, "rows": rows}
    finally:
        workbook.close()
