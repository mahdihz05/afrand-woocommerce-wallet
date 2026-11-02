"""Customer deduplication for direct credit; purchase multiplicity for cashback."""
import unittest
from datetime import date, datetime
import test_wallet
from test_wallet import workbook


class DuplicateModeTests(unittest.TestCase):
    setUp = test_wallet.WalletTests.setUp
    tearDown = test_wallet.WalletTests.tearDown

    def preview(self, rows, mode, headers=None):
        response = self.client.post('/api/import/preview', data={
            'file': (workbook(rows, headers or ['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند']), 'repeat.xlsx'),
            'amount_mode': mode, 'fixed_amount': '500', 'cashback_percent': '5'})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def finish(self, preview):
        offset = 0
        while offset < preview['valid_count']:
            response = self.client.post('/api/import/commit', json={
                'draft_id': preview['draft_id'], 'offset': offset, 'confirm': True})
            self.assertEqual(response.status_code, 200, response.json)
            offset = response.json['next_offset']
        return self.client.get('/api/import/' + preview['draft_id']).json

    def test_direct_three_rows_duplicate_mobile_credits_two_and_reports_skip(self):
        rows = [['مریم', '09121234567', '', 1000],
                ['جدید', '09120000002', '', 2000],
                ['مریم', '۰۹۱۲۱۲۳۴۵۶۷', '', 3000]]
        preview = self.preview(rows, 'fixed')
        self.assertEqual((preview['valid_count'], preview['invalid_count'], preview['skipped_count']), (2, 0, 1))
        self.assertEqual(preview['skipped_rows'][0]['row_number'], 4)
        self.assertEqual(preview['total'], '1000')
        self.finish(preview)
        self.assertEqual(len(self.woo.transactions), 2)
        self.finish(self.preview(list(reversed(rows)), 'fixed'))
        self.assertEqual(len(self.woo.transactions), 4)
        self.finish(self.preview(rows, 'fixed'))
        self.assertEqual(len(self.woo.transactions), 6)
        self.finish(preview)
        self.assertEqual(len(self.woo.transactions), 6)

    def test_direct_deduplicates_even_with_separate_explicit_documents(self):
        preview = self.preview([['A', 'مریم', '09121234567', 1000],
                                ['B', 'مریم', '09121234567', 1000]], 'fixed',
                               ['شماره سند', 'نام', 'شماره', 'مبلغ خرید'])
        self.assertEqual((preview['valid_count'], preview['skipped_count']), (1, 1))
        self.finish(preview)
        self.assertEqual(len(self.woo.transactions), 1)

    def test_cashback_five_identical_purchases_are_five_credits_and_replay(self):
        rows = [['مریم', '09121234567', '', 1000, f'BUY-{i}'] for i in range(5)]
        preview = self.preview(rows, 'excel')
        self.assertEqual((preview['valid_count'], preview['invalid_count'], preview['skipped_count']), (5, 0, 0))
        self.assertEqual(preview['total'], '250')
        self.assertEqual(len({r['reference'] for r in preview['valid_rows']}), 5)
        self.finish(preview)
        self.assertEqual(len(self.woo.transactions), 5)
        self.assertEqual(sum(int(t['amount']) for t in self.woo.transactions), 250)
        saved = self.finish(self.preview(rows, 'excel'))
        self.assertTrue(all(r['replayed'] for r in saved['results']))
        self.assertEqual(len(self.woo.transactions), 5)

    def test_cashback_requires_reference_column_and_nonblank_values(self):
        missing = self.preview([['مریم', '09121234567', '', 1000]], 'excel',
                               ['نام', 'شماره', 'شرکت', 'مبلغ خرید'])
        self.assertTrue(missing['mapping_required'])
        self.assertTrue(next(f for f in missing['fields'] if f['key'] == 'reference')['required'])
        blank = self.preview([['مریم', '09121234567', '', 1000, '']], 'excel')
        self.assertEqual((blank['valid_count'], blank['invalid_count']), (0, 1))
        self.assertIn('الزامی', blank['invalid_rows'][0]['errors'][0])
        self.assertFalse(self.woo.transactions)

    def test_new_document_allows_same_purchase_in_new_period(self):
        for reference in ('PERIOD-1', 'PERIOD-2', 'PERIOD-3'):
            self.finish(self.preview([['مریم', '09121234567', '', 1000, reference]], 'excel'))
        self.assertEqual(len(self.woo.transactions), 3)

    def test_cashback_accepts_dates_and_custom_identifiers_and_replays(self):
        references = ['۱۴۰۵/۰۸/۱۱-۱', 'تسویه/آبان-A', '2026-11-02',
                      datetime(2026, 11, 3), 0]
        rows = [['مریم', '09121234567', '', 1000, ref] for ref in references]
        preview = self.preview(rows, 'excel')
        self.assertEqual((preview['valid_count'], preview['invalid_count']), (5, 0))
        self.assertEqual([r['reference'] for r in preview['valid_rows']],
                         ['۱۴۰۵/۰۸/۱۱-۱', 'تسویه/آبان-A', '2026-11-02',
                          '2026-11-03T00:00:00', '0'])
        self.finish(preview)
        replay = self.finish(self.preview(list(reversed(rows)), 'excel'))
        self.assertTrue(all(r['replayed'] for r in replay['results']))
        self.assertEqual(len(self.woo.transactions), 5)
        self.assertEqual(sum(int(t['amount']) for t in self.woo.transactions), 250)

    def test_same_excel_date_is_duplicate_even_for_different_customers(self):
        preview = self.preview([
            ['مریم', '09121234567', '', 1000, date(2026, 11, 2)],
            ['دیگر', '09120000002', '', 2000, datetime(2026, 11, 2)],
        ], 'excel')
        self.assertEqual((preview['valid_count'], preview['invalid_count']), (0, 2))
        for row in preview['invalid_rows']:
            self.assertIn('تکراری', ' '.join(row['errors']))
        self.assertFalse(self.woo.transactions)

    def test_cashback_new_customer_created_once_for_repeated_purchases(self):
        rows = [['New customer', '09120000009', '', amount, f'NEW-{i}']
                for i, amount in enumerate((1000, 2000, 1000, 3000, 1000))]
        preview = self.preview(rows, 'excel')
        self.assertEqual(preview['valid_count'], 5)
        self.finish(preview)
        customers = [c for c in self.woo.customers if c['phone'] == '09120000009']
        self.assertEqual(len(customers), 1)
        self.assertEqual({t['customer_id'] for t in self.woo.transactions}, {customers[0]['id']})
        self.assertEqual(sum(int(t['amount']) for t in self.woo.transactions), 400)
        self.finish(self.preview(list(reversed(rows)), 'excel'))
        self.assertEqual(len(self.woo.transactions), 5)

    def test_explicit_duplicate_invoice_still_rejected_for_cashback(self):
        preview = self.preview([['A', 'مریم', '09121234567', 1000]] * 2, 'excel',
                               ['شماره سند', 'نام', 'شماره', 'مبلغ خرید'])
        self.assertEqual((preview['valid_count'], preview['invalid_count']), (0, 2))
        self.assertFalse(self.woo.transactions)


if __name__ == '__main__':
    unittest.main()
