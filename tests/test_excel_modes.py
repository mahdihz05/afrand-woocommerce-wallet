"""Exercise the two import modes through HTTP against a synthetic wallet."""
import unittest
import test_wallet
from test_wallet import workbook


class ExcelModeTests(unittest.TestCase):
    setUp = test_wallet.WalletTests.setUp
    tearDown = test_wallet.WalletTests.tearDown

    def preview(self, rows, headers=None, **options):
        file = workbook(rows, headers) if headers else workbook(rows, ['نام', 'شماره', 'شرکت', 'مبلغ خرید'])
        if options.pop('no_header', False):
            from openpyxl import load_workbook
            import io
            book = load_workbook(file); book.active.delete_rows(1)
            file = io.BytesIO(); book.save(file); file.seek(0)
        response = self.client.post('/api/import/preview', data={'file': (file, 'staff.xlsx'), **options})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def commit(self, data):
        response = self.client.post('/api/import/commit', json={'draft_id': data['draft_id'], 'confirm': True})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_headers_no_headers_reorder_and_numeric_format_replay(self):
        rows = [['الف', '09121234567', 'شرکت', 1000], ['ب', '09123334444', 'شرکت', 2000]]
        first = self.preview(rows)
        self.assertEqual(first['valid_count'], 2)
        self.assertEqual(first['header_row'], 1)
        self.assertEqual(self.commit(first)['created'], 2)
        rows[0][3] = '۱٬۰۰۰'
        again = self.preview(list(reversed(rows)), no_header=True)
        self.assertEqual(again['header_row'], 0)
        self.assertEqual(again['valid_count'], 2)
        self.assertEqual(self.commit(again)['replayed'], 2)
        self.assertEqual(len(self.woo.transactions), 2)

    def test_fixed_full_credit_ignores_purchase_and_percent(self):
        for no_header in (False, True):
            data = self.preview([['الف', '09121234567', 'شرکت', 'invalid']], no_header=no_header,
                                amount_mode='fixed', fixed_amount='1000000', cashback_percent='invalid')
            self.assertEqual(data['valid_count'], 1)
            self.assertEqual(data['total'], '1000000')
            result = self.commit(data)
            self.assertEqual(result['needs_review'], 0)
        self.assertEqual(len(self.woo.transactions), 1)
        self.assertIn('شارژ مستقیم', result['results'][0]['note'])

    def test_duplicate_staff_rows_all_rejected(self):
        row = ['الف', '09121234567', 'شرکت', 1000]
        data = self.preview([row, row], amount_mode='fixed', fixed_amount='1000')
        self.assertEqual(data['invalid_count'], 2)
        self.assertEqual(data['valid_count'], 0)

    def test_fixed_lost_response_retry_reconciles(self):
        data = self.preview([['الف', '09121234567', '', 1]], amount_mode='fixed', fixed_amount='1000')
        self.woo.lose_credit_response = True
        self.assertEqual(self.commit(data)['needs_review'], 1)
        self.assertEqual(self.commit(data)['needs_review'], 0)
        self.assertEqual(len(self.woo.transactions), 1)

    def test_changed_percent_on_same_purchase_requires_review(self):
        row = [['الف', '09121234567', '', 1000]]
        self.commit(self.preview(row))
        result = self.commit(self.preview(row, cashback_percent='10'))
        self.assertEqual(result['needs_review'], 1)
        self.assertEqual(len(self.woo.transactions), 1)

    def test_fixed_without_amount_column_and_invalid_fixed(self):
        data = self.preview([['الف', '09121234567', '']], headers=['نام', 'شماره', 'شرکت'], amount_mode='fixed', fixed_amount='100')
        self.assertEqual(data['valid_count'], 1)
        response = self.client.post('/api/import/preview', data={'file': (workbook([['a','09121234567','']], ['نام','شماره','شرکت']), 'a.xlsx'), 'amount_mode': 'fixed', 'fixed_amount': '0'})
        self.assertEqual(response.status_code, 400)
