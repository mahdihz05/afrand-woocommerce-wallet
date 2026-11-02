"""Company column contracts, through the synthetic import HTTP flow."""
import io
import unittest
from openpyxl import load_workbook
import test_wallet
from test_wallet import workbook


class ExcelCompanyTests(unittest.TestCase):
    setUp = test_wallet.WalletTests.setUp
    tearDown = test_wallet.WalletTests.tearDown

    def preview(self, reference, phone, company=None, alias='شرکت'):
        headers=['شماره سند','نام مشتری','موبایل','مبلغ خرید','واحد پول']
        row=[reference,'مشتری شرکت',phone,1000,'IRT']
        if company is not None:
            headers.append(alias); row.append(company)
        response=self.client.post('/api/import/preview',data={'file':(workbook([row],headers),'company.xlsx')})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['valid_count'],1)
        return response.json

    def commit(self, preview):
        response=self.client.post('/api/import/commit',json={'draft_id':preview['draft_id'],'confirm':True})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['needs_review'],0)
        return response.json

    def test_aliases_save_new_company_and_retry_does_not_credit_twice(self):
        for index,alias in enumerate(['شرکت','نام شرکت','company','billing_company']):
            with self.subTest(alias=alias):
                phone=f'0912333445{index}'
                preview=self.preview(f'CO-{index}',phone,'شرکت نمونه',alias)
                self.assertEqual(preview['valid_rows'][0]['company'],'شرکت نمونه')
                self.assertEqual(self.commit(preview)['created'],1)
                self.assertEqual(self.woo.customers[-1]['company'],'شرکت نمونه')
                self.assertEqual(self.commit(self.preview(f'CO-{index}',phone,'شرکت نمونه',alias))['replayed'],1)
        self.assertEqual(len(self.woo.transactions),4)

    def test_existing_company_different_blank_missing_are_preserved_and_explained(self):
        self.woo.customers[0]['company']='شرکت اصلی'
        for index,company in enumerate(['شرکت دیگر','',None]):
            preview=self.preview(f'EX-{index}','09121234567',company)
            row=preview['valid_rows'][0]
            self.assertEqual(row['existing_company'],'شرکت اصلی')
            self.assertIn('حفظ می‌شود',row['company_notice'])
            self.commit(preview)
            self.assertEqual(self.woo.customers[0]['company'],'شرکت اصلی')
        self.assertEqual(len(self.woo.transactions),3)

    def test_missing_optional_column_new_customer(self):
        preview=self.preview('NO-COMP','09123330000')
        self.commit(preview)
        self.assertEqual(self.woo.customers[-1]['company'],'')

    def test_downloads_have_company_and_sample_is_importable(self):
        sample_data = None
        for path in ['/template.xlsx','/demo.xlsx']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            data=response.data
            response.close()
            sheet=load_workbook(io.BytesIO(data)).active
            self.assertIn('شرکت',[c.value for c in sheet[1]])
            if path == '/demo.xlsx':
                sample_data = data
        response=self.client.post('/api/import/preview',data={'file':(io.BytesIO(sample_data),'sample.xlsx')})
        self.assertEqual(response.json['valid_count'],3)
        self.assertTrue(all(row['company'] for row in response.json['valid_rows']))
