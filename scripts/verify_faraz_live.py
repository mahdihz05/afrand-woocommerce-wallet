"""Explicit, bounded Faraz staging acceptance; never run against production customers."""
import argparse
import io
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook
from panel.app import create_app
from panel.woo import WooError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-staging', action='store_true')
    if not parser.parse_args().live_staging:
        parser.error('Explicit --live-staging is required (creates two marked customers, 203 IRT total).')
    from run import app as configured
    woo = configured.extensions['woo']
    assert woo.base_url == 'https://www.afrandacademy.ir' and woo.host_matches_staging
    token = uuid.uuid4().hex
    out = Path('instance/verification/faraz') / token
    out.mkdir(parents=True)
    config = dict(configured.config, TESTING=True, PANEL_AUTH_ENABLED=False, DATABASE=str(out/'acceptance.db'))
    app = create_app(config, woo_client=woo)
    client = app.test_client()
    report = {'run': token, 'checks': [], 'customers': [], 'credits_total_irt': 203}
    def check(name):
        report['checks'].append(name)
        (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print('PASS '+name, flush=True)
    status = client.get('/api/status')
    assert (status.status_code == 200 and status.json['wallet'] == 'FarazSMS'
            and status.json['currency'] == 'IRT'), status.json
    check('authenticated_rest_provider_currency')
    phones = ['0999'+str((int(token[:10],16)+i)%10000000).zfill(7) for i in (0,1)]
    for phone in phones:
        assert not woo.request('GET','afrand-faraz-wallet/customers',params={'q':phone})['customers']
        r = client.post('/api/customers',json={'first_name':'آزمایشی فراز','last_name':token[:8], 'phone':phone,'confirm':True})
        assert r.status_code == 200, r.json
        report['customers'].append(r.json['customer']['id'])
    ids = report['customers']
    old = [woo.request('GET',f'afrand-wallet/customers/{id}')['wallet']['balance'] for id in ids]
    payload = {'request_id':token,'customer_id':ids[0],'purchase_amount':'100','cashback_percent':'5','currency':'IRT','note':'Faraz staging '+token[:8],'confirm':True}
    original = woo.request
    def lose_response(method,path,**kwargs):
        result = original(method,path,**kwargs)
        if method == 'POST' and path == 'afrand-faraz-wallet/credits':
            raise WooError('Synthetic lost response AFTER real native credit',category='connection')
        return result
    woo.request = lose_response
    try:
        assert client.post('/api/wallet/credits',json=payload).status_code == 502
    finally:
        woo.request = original
    r = client.get('/api/wallet/operations/'+token)
    assert r.status_code == 200 and r.json['state']=='succeeded' and r.json['amount']=='5', r.json
    r = client.post('/api/wallet/credits',json=payload)
    assert r.status_code == 200 and r.json['replayed'], r.json
    assert client.post('/api/wallet/credits',json={**payload,'purchase_amount':'200'}).status_code == 409
    check('native_credit_lost_response_recovered_replay_and_conflict')
    def preview(mode,rows):
        book = Workbook(); sheet = book.active
        sheet.append(['نام','شماره','شرکت','مبلغ خرید','شماره سند'])
        for row in rows: sheet.append(row)
        data = io.BytesIO(); book.save(data); data.seek(0)
        r = client.post('/api/import/preview',data={'file':(data,'faraz-test.xlsx'),'amount_mode':mode,'fixed_amount':'37','cashback_percent':'10'})
        assert r.status_code == 200 and r.json['invalid_count']==0, r.json
        return r.json
    def finish(p):
        offset=0; results=[]
        while True:
            r=client.post('/api/import/commit',json={'draft_id':p['draft_id'],'offset':offset,'confirm':True})
            assert r.status_code==200 and r.json['needs_review']==0, r.json
            results.extend(r.json['results']); offset=r.json['next_offset']
            if r.json['done']: return results
    fixed = [['آزمایشی فراز',phones[i],'',100,''] for i in (0,0,1)]
    for _ in range(2):
        p=preview('fixed',fixed); assert p['valid_count']==2, p
        finish(p)
    check('fixed_duplicate_phone_once_per_upload_two_independent_uploads')
    rows=[['آزمایشی فراز',phones[0],'',100,f'{token}-{i}'] for i in range(5)]
    p=preview('excel',rows); assert p['valid_count']==5
    finish(p); finish(preview('excel',list(reversed(rows))))
    check('cashback_five_purchases_same_customer_and_document_replay')
    balances=[]; txids=[]
    for id, expected in zip(ids,(129,74)):
        wallet=woo.request('GET',f'afrand-faraz-wallet/customers/{id}')['wallet']
        assert Decimal(wallet['balance'])==expected, wallet
        assert sum(Decimal(t['amount']) for t in wallet['transactions'])==expected, wallet
        assert all(Decimal(t['used_amount'])==0 and t['status']=='active' for t in wallet['transactions'])
        balances.append(wallet['balance']); txids.extend(t['transaction_id'] for t in wallet['transactions'])
    assert [woo.request('GET',f'afrand-wallet/customers/{id}')['wallet']['balance'] for id in ids]==old
    report.update(balances=balances,transactions=txids)
    check('native_faraz_balance_and_ledger_exact_203_irt_terawallet_unchanged')
    print(json.dumps(report,ensure_ascii=True))


if __name__ == '__main__':
    main()
