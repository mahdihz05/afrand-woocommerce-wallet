"""Delivery contracts through the HTTP API; all wallet calls are synthetic."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from panel.app import create_app
import test_wallet
from test_wallet import workbook


class DeliveryTests(unittest.TestCase):
    setUp = test_wallet.WalletTests.setUp
    tearDown = test_wallet.WalletTests.tearDown
    preview = test_wallet.WalletTests.preview
    def test_company_explicit_update_compare_and_swap_and_import_no_overwrite(self):
        created=self.client.post('/api/customers',json={'first_name':'Company customer','phone':'09123334455','company':'Original','confirm':True}).json
        customer=created['customer']; self.assertEqual(customer['company'],'Original')
        replay=self.client.post('/api/customers',json={'first_name':'Other','phone':'09123334455','company':'Implicit','confirm':True}).json
        self.assertEqual(replay['customer']['company'],'Original')
        path=f"/api/customers/{customer['id']}/company"
        self.assertEqual(self.client.put(path,json={'company':'New','expected_company':'Original'}).status_code,400)
        self.assertEqual(self.client.put(path,json={'company':'New','expected_company':'Original','confirm':True}).status_code,200)
        self.assertEqual(self.client.put(path,json={'company':'Stale','expected_company':'Original','confirm':True}).status_code,409)
        headers=['reference','customer_name','phone','amount','currency','company']
        data=self.client.post('/api/import/preview',data={'file':(workbook([['COMP','Company customer','09123334455',100,'IRT','Implicit']],headers),'company.xlsx')}).json
        self.assertEqual(data['valid_count'],1)
        self.client.post('/api/import/commit',json={'draft_id':data['draft_id'],'confirm':True})
        self.assertEqual(self.client.get(f"/api/customers/{customer['id']}").json['customer']['company'],'New')
        new=self.client.post('/api/import/preview',data={'file':(workbook([['NEWCOMP','New customer','09123334456',100,'IRT','From Excel']],headers),'new.xlsx')}).json
        result=self.client.post('/api/import/commit',json={'draft_id':new['draft_id'],'confirm':True}).json
        self.assertEqual(result['created'],1)
        self.assertEqual(self.woo.customers[-1]['company'],'From Excel')

    def test_thousand_unique_phone_lookup_is_bounded(self):
        result=self.preview([[f'R{i}','Customer',f'0912{i:07d}',100,'IRT'] for i in range(1000)])
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['valid_count'],1000)
        self.assertEqual(self.woo.calls.count(('POST','afrand-wallet/customers/lookup')),10)

    def test_restart_restores_partial_results_and_reconciles_decimal_lost_response(self):
        preview=self.preview([[f'RESTART{i}','Customer','09121234567',100,'IRT'] for i in range(4)]).json
        draft=preview['draft_id']; self.woo.lose_credit_response=True
        first=self.client.post('/api/import/commit',json={'draft_id':draft,'confirm':True}).json
        self.assertEqual((first['created'],first['needs_review']),(1,1))
        for operation in self.woo.operations.values(): operation['amount']=str(Decimal(operation['amount']).quantize(Decimal('.01')))
        self.app=create_app(self.config,woo_client=self.woo); self.client=self.app.test_client()
        restored=self.client.get('/api/import/'+draft).json
        self.assertEqual(len(restored['results']),2); self.assertEqual(restored['next_offset'],0)
        self.assertTrue(restored['confirmed']); self.assertFalse(restored['done'])
        writes_before=self.woo.calls.count(('POST','afrand-wallet/credits'))
        recovered=self.client.post('/api/import/commit',json={'draft_id':draft,'confirm':True,'offset':0}).json
        self.assertEqual(recovered['replayed'],2)
        self.assertEqual(self.woo.calls.count(('POST','afrand-wallet/credits')),writes_before)
        self.client.post('/api/import/commit',json={'draft_id':draft,'confirm':True,'offset':2})
        self.assertTrue(self.client.get('/api/import/'+draft).json['done'])
        self.assertEqual(len(self.woo.transactions),4)
        self.assertEqual(self.client.get('/api/imports').json['imports'][0]['draft_id'],draft)

    def test_document_conflict_note_and_phone_never_credits_again(self):
        headers=['reference','customer_name','phone','amount','currency','note']
        for phone,note in [('09121234567','Original'),('09121234567','Changed'),('09123334455','Original')]:
            p=self.client.post('/api/import/preview',data={'file':(workbook([['CONFLICT','Name',phone,100,'IRT',note]],headers),'test.xlsx')}).json
            result=self.client.post('/api/import/commit',json={'draft_id':p['draft_id'],'confirm':True}).json
        self.assertEqual(result['needs_review'],1); self.assertEqual(len(self.woo.transactions),1)
        self.assertEqual(len(self.woo.customers),1)

    def test_sanitized_title_lost_response_recovers_once_after_restart(self):
        # Explicit synthetic WordPress sanitizer outputs, not an implementation
        # of sanitize_text_field. PHP separately tests the verification contract.
        cases = [('<b>Sale</b>', 'Sale'), ('خرید <b>پاییز</b>', 'خرید پاییز'),
                 ('Sale\t \n autumn', 'Sale autumn'),
                 ('Sale &amp; &#160; autumn', 'Sale &amp; &#160; autumn')]
        headers=['reference','customer_name','phone','amount','currency','note']
        for index, (original, canonical) in enumerate(cases):
            with self.subTest(title=original):
                p=self.client.post('/api/import/preview',data={'file':(workbook(
                    [[f'SANITIZED-{index}','Name','09121234567',100,'IRT',original]],headers),'test.xlsx')}).json
                request=self.woo.request
                def sanitized_remote(method, path, params=None, payload=None):
                    if method=='POST' and path=='afrand-wallet/credits':
                        payload={**payload,'note':payload['note'].replace(original,canonical)}
                    result=request(method,path,params=params,payload=payload)
                    if path.startswith('afrand-wallet/operations/') and params and 'expected_note' in params:
                        expected=params['expected_note']
                        if expected.replace(original,canonical)!=result['note']:
                            raise test_wallet.WooError('title conflict',409)
                        result['verified_note']=expected
                    return result
                with patch.object(self.woo,'request',side_effect=sanitized_remote):
                    self.woo.lose_credit_response=True
                    first=self.client.post('/api/import/commit',json={'draft_id':p['draft_id'],'confirm':True}).json
                    self.assertEqual(first['needs_review'],1)
                    self.app=create_app(self.config,woo_client=self.woo); self.client=self.app.test_client()
                    writes=self.woo.calls.count(('POST','afrand-wallet/credits'))
                    for _ in range(2):
                        recovered=self.client.post('/api/import/commit',json={'draft_id':p['draft_id'],'confirm':True}).json
                        self.assertEqual(recovered['needs_review'],0)
                        self.assertEqual(recovered['results'][0]['state'],'succeeded')
                    self.assertEqual(self.woo.calls.count(('POST','afrand-wallet/credits')),writes)
                self.assertEqual(len(self.woo.transactions),index+1)

    def test_recovery_rejects_conflicting_remote_title(self):
        p=self.preview([['REMOTE-CONFLICT','Name','09121234567',100,'IRT']]).json
        self.woo.lose_credit_response=True
        self.client.post('/api/import/commit',json={'draft_id':p['draft_id'],'confirm':True})
        operation=next(iter(self.woo.operations.values()))
        operation['note']='A genuinely different transaction'
        for _ in range(2):
            result=self.client.post('/api/import/commit',json={'draft_id':p['draft_id'],'confirm':True}).json
            self.assertEqual(result['needs_review'],1)
        self.assertEqual(len(self.woo.transactions),1)
        self.assertEqual(self.woo.calls.count(('POST','afrand-wallet/credits')),1)
