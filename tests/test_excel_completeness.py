"""Reconcile every input row and customer, including full-capacity imports."""
import unittest
import io
from decimal import Decimal

from openpyxl import load_workbook
import test_wallet
from test_wallet import workbook
from panel.woo import WooError


class ExcelCompletenessTests(unittest.TestCase):
    setUp = test_wallet.WalletTests.setUp
    tearDown = test_wallet.WalletTests.tearDown

    def preview(self, rows, mode):
        response = self.client.post('/api/import/preview', data={
            'file': (workbook(rows, ['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند']), 'complete.xlsx'),
            'amount_mode': mode, 'fixed_amount': '37', 'cashback_percent': '7.5'})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def batch(self, draft, offset):
        response = self.client.post('/api/import/commit', json={
            'draft_id': draft, 'offset': offset, 'confirm': True})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def finish(self, preview, offset=0):
        visited = []
        while True:
            result = self.batch(preview['draft_id'], offset)
            visited.extend(row['reference'] for row in result['results'])
            self.assertGreater(result['next_offset'], offset)
            offset = result['next_offset']
            if result['done']:
                break
        return visited

    def assert_balances(self, expected):
        customers = {c['phone']: c['id'] for c in self.woo.customers}
        actual = {}
        for phone, amount in expected.items():
            self.assertIn(phone, customers)
            rows = [t for t in self.woo.transactions if t['customer_id'] == customers[phone]]
            self.assertEqual(len(rows), 1, phone)
            actual[phone] = sum(Decimal(t['amount']) for t in rows)
        self.assertEqual(actual, expected)

    def test_full_1000_customers_in_both_modes_every_row_exactly_once(self):
        self.woo.currency = 'IRR'
        for mode in ('excel', 'fixed'):
            with self.subTest(mode=mode):
                # Separate phones and documents for each mode; mix existing/new.
                prefix = '0913' if mode == 'excel' else '0914'
                rows = [[f'مشتری {i}', prefix + f'{i:07d}', 'شرکت آزمایش', 1001 + i, f'FULL-{i}']
                        for i in range(1000)]
                for i in range(0, 1000, 3):
                    self.woo.customers.append({'id': 10000 + len(self.woo.customers),
                        'first_name': 'موجود', 'last_name': '', 'phone': rows[i][1]})
                expected = {row[1]: Decimal(37 if mode == 'fixed' else
                            (row[3] * 75 // 1000)) for row in rows}
                before = len(self.woo.transactions)
                preview = self.preview(rows, mode)
                self.assertEqual((preview['valid_count'], preview['invalid_count']), (1000, 0))
                self.assertEqual(Decimal(preview['total']), sum(expected.values()))
                self.assertEqual(len(self.woo.transactions), before)
                visited = self.finish(preview)
                self.assertEqual(len(visited), 1000)
                self.assertEqual(len(set(visited)), 1000)
                self.assertEqual(len(self.woo.transactions) - before, 1000)
                self.assert_balances(expected)
                saved = self.client.get('/api/import/' + preview['draft_id']).json
                self.assertTrue(saved['done'])
                self.assertEqual(saved['next_offset'], 1000)
                self.assertEqual(len(saved['results']), 1000)
                self.assertTrue(all(row.get('transaction_id') for row in saved['results']))
                # A newly uploaded reversed workbook must also visit all 500 batches.
                replay = self.preview(list(reversed(rows)), mode)
                self.assertEqual(len(self.finish(replay)), 1000)
                self.assertEqual(len(self.woo.transactions) - before, 2000 if mode == 'fixed' else 1000)

    def test_middle_credit_failure_continues_and_resume_fills_only_gap_both_modes(self):
        original = self.woo.request
        for mode in ('excel', 'fixed'):
            with self.subTest(mode=mode):
                prefix = '0915' if mode == 'excel' else '0916'
                rows = [[f'مشتری {i}', prefix + f'{i:07d}', '', 1001 + i, f'GAP-{i}'] for i in range(7)]
                preview = self.preview(rows, mode)
                failed_phone = rows[3][1]

                def request(method, path, params=None, payload=None):
                    if path == 'afrand-wallet/credits':
                        customer = next(c for c in self.woo.customers if c['id'] == payload['customer_id'])
                        if customer['phone'] == failed_phone:
                            raise WooError('temporary credit outage', 503)
                    return original(method, path, params=params, payload=payload)

                self.woo.request = request
                self.finish(preview)
                saved = self.client.get('/api/import/' + preview['draft_id']).json
                self.assertFalse(saved['done'])
                self.assertEqual(saved['next_offset'], 3)
                self.assertEqual(len(saved['results']), 7)
                self.assertEqual(sum(bool(r.get('transaction_id')) for r in saved['results']), 6)
                self.assertEqual(saved['results'][3]['state'], 'needs_review')
                self.woo.request = original
                self.finish(preview, saved['next_offset'])
                saved = self.client.get('/api/import/' + preview['draft_id']).json
                self.assertTrue(saved['done'])
                self.assertEqual(len(saved['results']), 7)
                self.assert_balances({row[1]: Decimal(37 if mode == 'fixed' else
                                     row[3] * 75 // 1000) for row in rows})

    def test_invalid_and_blank_rows_are_accounted_with_physical_numbers_both_modes(self):
        for mode in ('excel', 'fixed'):
            with self.subTest(mode=mode):
                rows = [['اول', '09170000001', '', 1000, 'VALID-1'], [None] * 4,
                        ['خراب', '123', '', 1000], ['بدون نام', '', '', 1000],
                        ['آخر', '09170000002', '', 2000, 'VALID-2']]
                preview = self.preview(rows, mode)
                self.assertEqual((preview['valid_count'], preview['invalid_count']), (2, 2))
                self.assertEqual({r['row_number'] for r in preview['valid_rows']}, {2, 6})
                self.assertEqual({r['row_number'] for r in preview['invalid_rows']}, {4, 5})
                self.assertTrue(all(r['errors'] for r in preview['invalid_rows']))
                self.finish(preview)

    def test_1001_rows_reject_entire_file_without_any_customer_or_credit_writes(self):
        for mode in ('excel', 'fixed'):
            rows = [[f'مشتری {i}', '0918' + f'{i:07d}', '', 1000, f'LIMIT-{i}'] for i in range(1001)]
            response = self.client.post('/api/import/preview', data={
                'file': (workbook(rows, ['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند']), 'too-large.xlsx'),
                'amount_mode': mode, 'fixed_amount': '37'})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(len(self.woo.customers), 1)
            self.assertFalse(self.woo.transactions)

    def test_multiple_sheets_cannot_silently_omit_customers_in_either_mode(self):
        book = load_workbook(workbook([['اول', '09190000001', '', 1000]],
                                     ['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند']))
        other = book.create_sheet('بخش دوم')
        other.append(['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند'])
        other.append(['دوم', '09190000002', '', 2000])
        for mode in ('excel', 'fixed'):
            stream = io.BytesIO()
            book.save(stream)
            stream.seek(0)
            response = self.client.post('/api/import/preview', data={
                'file': (stream, 'two-sheets.xlsx'), 'amount_mode': mode, 'fixed_amount': '37'})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertIn('برگه', response.json['error'])
            self.assertEqual(len(self.woo.customers), 1)
            self.assertFalse(self.woo.transactions)


if __name__ == '__main__':
    unittest.main()
