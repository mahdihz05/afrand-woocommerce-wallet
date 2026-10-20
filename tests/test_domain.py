import unittest

from panel.domain import find_customer_by_phone, normalize_phone, validate_import_rows


class PhoneMatchingTests(unittest.TestCase):
    def test_normalizes_persian_digits_and_iranian_country_prefix(self):
        self.assertEqual(normalize_phone("+۹۸ (۹۱۲) ۱۲۳-۴۵۶۷"), "09121234567")

    def test_returns_existing_customer_for_one_exact_phone_match(self):
        customers = [{"id": 11, "phone": "0912 123 4567"}]
        self.assertEqual(find_customer_by_phone("+98 9121234567", customers), ("existing", customers[0]))

    def test_refuses_ambiguous_duplicate_phone_instead_of_creating(self):
        customers = [{"id": 11, "phone": "09121234567"}, {"id": 12, "phone": "+989121234567"}]
        self.assertEqual(find_customer_by_phone("09121234567", customers)[0], "ambiguous")

    def test_marks_unmatched_phone_as_new_customer(self):
        self.assertEqual(find_customer_by_phone("09125556677", [])[0], "new")


class SpreadsheetValidationTests(unittest.TestCase):
    def test_fractional_quantity_and_malformed_email_are_invalid(self):
        row = {'order_ref': 'A-1', 'customer_name': 'مریم', 'customer_phone': '09121234567',
               'product_sku': 'SKU-1', 'quantity': 1.5, 'customer_email': 'invalid'}
        valid, invalid = validate_import_rows([row], {'SKU-1': {'id': 101}})
        self.assertEqual(valid, [])
        self.assertEqual(len(invalid[0]['errors']), 3)  # quantity, email, whole-order rejection

    def test_real_spreadsheet_row_number_is_preserved(self):
        row = {'row_number': 8, 'order_ref': 'A-1', 'customer_name': 'مریم', 'customer_phone': '09121234567',
               'product_sku': 'SKU-1', 'quantity': 1}
        valid, _ = validate_import_rows([row], {'SKU-1': {'id': 101}})
        self.assertEqual(valid[0]['row_number'], 8)
    def test_reports_invalid_rows_but_keeps_valid_rows(self):
        rows = [
            {"order_ref": "A-1", "customer_name": "مریم نمونه", "customer_phone": "09121234567", "product_sku": "SKU-1", "quantity": 2},
            {"order_ref": "A-2", "customer_name": "علی نمونه", "customer_phone": "", "product_sku": "SKU-1", "quantity": 1},
            {"order_ref": "A-3", "customer_name": "سارا نمونه", "customer_phone": "09125556677", "product_sku": "MISSING", "quantity": 1},
        ]
        valid, invalid = validate_import_rows(rows, {"SKU-1": {"id": 101, "name": "محصول نمونه"}})
        self.assertEqual([r["order_ref"] for r in valid], ["A-1"])
        self.assertEqual([r["row_number"] for r in invalid], [3, 4])
        self.assertIn("تلفن", " ".join(invalid[0]["errors"]))
        self.assertIn("SKU", " ".join(invalid[1]["errors"]))

    def test_rejects_nonpositive_quantity(self):
        rows = [{"order_ref": "A-1", "customer_name": "مریم", "customer_phone": "09121234567", "product_sku": "SKU-1", "quantity": 0}]
        valid, invalid = validate_import_rows(rows, {"SKU-1": {"id": 101, "name": "محصول نمونه"}})
        self.assertEqual(valid, [])
        self.assertTrue(any("تعداد" in e for e in invalid[0]["errors"]))

    def test_groups_order_lines_by_import_reference_and_rejects_customer_conflicts(self):
        rows = [
            {"order_ref": "A-1", "customer_name": "مریم", "customer_phone": "09121234567", "product_sku": "SKU-1", "quantity": 1},
            {"order_ref": "A-1", "customer_name": "مریم", "customer_phone": "09121234567", "product_sku": "SKU-2", "quantity": 2},
            {"order_ref": "A-2", "customer_name": "علی", "customer_phone": "09125556677", "product_sku": "SKU-1", "quantity": 1},
        ]
        valid, invalid = validate_import_rows(rows, {"SKU-1": {"id": 101}, "SKU-2": {"id": 102}})
        self.assertEqual(len(valid), 3)
        self.assertEqual(invalid, [])
        # The import reference lets the service group multiple product rows into one order.
        self.assertEqual(len({r["order_ref"] for r in valid}), 2)
    def test_one_invalid_line_blocks_the_entire_grouped_order(self):
        rows = [
            {"order_ref": "A-7", "customer_name": "مریم", "customer_phone": "09121234567", "product_sku": "SKU-1", "quantity": 1},
            {"order_ref": "A-7", "customer_name": "مریم", "customer_phone": "09121234567", "product_sku": "UNKNOWN", "quantity": 1},
        ]
        valid, invalid = validate_import_rows(rows, {"SKU-1": {"id": 101}})
        self.assertEqual(valid, [])
        self.assertEqual(len(invalid), 2)
        self.assertTrue(all(any("کل سفارش" in error for error in row["errors"]) for row in invalid))


if __name__ == "__main__":
    unittest.main()
