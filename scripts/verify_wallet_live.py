"""Opt-in hosted wallet acceptance with bounded, marked staging credits."""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from playwright.sync_api import sync_playwright, expect
from run import app
from test_wallet import workbook


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-staging',action='store_true',help='Create two marked customers and small staging wallet credits, then verify replay safety')
    args=parser.parse_args()
    if not args.live_staging: parser.error('Use --live-staging explicitly; this test writes only to the configured dedicated staging.')
    woo=app.extensions['woo']; assert woo.host_matches_staging and woo.base_url=='https://www.wprokad.ir'
    base=os.environ.get('AFRAND_DEMO_URL','https://wp-woo-demo.mahdihz.ir').rstrip('/')
    assert base in {'https://wp-woo-demo.mahdihz.ir','http://141.11.250.83:8087'}
    run=uuid.uuid4().hex[:8]; phones=['0912777'+str((int(run,16)+i)%10000).zfill(4) for i in (0,1)]
    for phone in phones: assert not woo.request('GET','afrand-wallet/customers',params={'q':phone})['customers']
    fixture=woo.request('GET','afrand-wallet/customers',params={'q':'09128880787'})['customers']
    assert len(fixture)==1 and fixture[0]['last_name']=='افرند 20356763', 'Marked staging fixture must match before writes'
    existing={'customer_id':fixture[0]['id'],'phone':fixture[0]['phone']}
    existing_customer=woo.request('GET',f"afrand-wallet/customers/{existing['customer_id']}")
    prior=float(existing_customer['wallet']['balance'])
    out=Path('instance/verification/wallet-live')/run; out.mkdir(parents=True,exist_ok=True)
    report={'run':run,'url':base,'checks':[],'customers':[],'transactions':[],'orders_created':0,'javascript_errors':[]}
    def passed(name): report['checks'].append(name); (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print('PASS '+name,flush=True)
    ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True); context=browser.new_context(user_agent=ua,viewport={'width':1366,'height':950},reduced_motion='reduce')
        anonymous=context.request.get(base+'/api/status'); assert anonymous.status==401 and 'www-authenticate' not in anonymous.headers
        page=context.new_page(); page.on('pageerror',lambda error:report['javascript_errors'].append(str(error)))
        native=[]; page.on('dialog',lambda dialog:(native.append(dialog.type),dialog.dismiss()))
        page.goto(base,wait_until='networkidle'); page.locator('#username').fill('demo'); page.locator('#password').fill(Path('instance/demo-password').read_text().strip()); page.get_by_role('button',name='ورود',exact=True).click(); page.wait_for_url(base+'/')
        expect(page.locator('#connection-state')).to_contain_text('واریز فعال',timeout=25000)
        assert page.locator('input[type=email]').count()==0
        assert context.request.get(base+'/api/products').status==404
        assert context.request.post(base+'/api/orders',headers={'Origin':base},data={}).status==404
        page.screenshot(path=str(out/'desktop.png'),full_page=True); passed('hosted_login_wallet_only_no_email_no_orders')

        page.locator('#show-new-customer').click()
        for field,value in {'first_name':'آزمایشی دمو کیف پول','last_name':'افرند '+run,'phone':phones[0]}.items(): page.locator(f'#new-customer-form [name={field}]').fill(value)
        with page.expect_response(lambda r:r.url.endswith('/api/customers') and r.request.method=='POST',timeout=45000) as pending:
            page.locator('#save-customer-button').click(); page.locator('#confirm-accept').click()
        result=pending.value.json(); assert pending.value.ok,result
        created=result['customer']; report['customers'].append(created['id'])
        expect(page.locator('#selected-customer')).to_contain_text(run); expect(page.locator('#wallet-balance')).to_contain_text('۰ تومان',timeout=25000)
        raw=woo.request('GET',f"customers/{created['id']}"); assert raw['email']=='' and raw['billing']['phone']==phones[0]
        repeat=context.request.post(base+'/api/customers',headers={'Origin':base},data={'first_name':'آزمایشی دمو کیف پول','last_name':'افرند '+run,'phone':phones[0],'confirm':True})
        assert repeat.ok and repeat.json()['replayed'] and repeat.json()['customer']['id']==created['id']
        passed('wordpress_customer_created_without_email_same_phone_replays')

        lost={}
        def lose_response(route):
            response=route.fetch(timeout=60000); assert response.ok,response.text()
            lost['result']=response.json(); lost['payload']=route.request.post_data_json
            (out/'lost-response.json').write_text(json.dumps(lost,ensure_ascii=False,indent=2),encoding='utf-8')
            route.abort('failed')
        page.route('**/api/wallet/credits',lose_response,times=1)
        page.locator('#credit-amount').fill('۲۰٬۰۰۰'); page.locator('#credit-note').fill('آزمون بازیابی واریز افرند '+run)
        page.locator('#submit-credit').click(); expect(page.locator('#confirm-description')).to_contain_text('۱٬۰۰۰ تومان'); page.locator('#confirm-accept').click()
        expect(page.locator('#global-message')).to_contain_text('ارتباط',timeout=60000); expect(page.locator('#pending-credit')).to_be_visible()
        assert lost['result']['replayed'] is False
        assert lost['result']['amount']=='1000' and lost['result']['purchase_amount']=='20000' and lost['result']['cashback_percent']=='5'
        page.reload(wait_until='networkidle'); expect(page.locator('#retry-credit')).to_be_enabled(timeout=25000); page.locator('#retry-credit').click()
        expect(page.locator('#credit-result')).to_contain_text('دوباره واریز نشد',timeout=45000)
        expect(page.locator('#wallet-balance')).to_contain_text('۱٬۰۰۰ تومان',timeout=25000)
        native_transaction=woo.request('GET',f"wallet/{lost['result']['transaction_id']}"); assert int(native_transaction['user_id'])==created['id'] and float(native_transaction['amount'])==1000
        detail=woo.request('GET',f"afrand-wallet/customers/{created['id']}"); assert float(detail['wallet']['balance'])==1000 and len(detail['wallet']['transactions'])==1
        report['transactions'].append(lost['result']['transaction_id']); passed('lost_credit_response_reload_native_reconcile_no_double_credit')

        page.locator('#customer-query').fill(existing['phone'])
        page.locator('#customer-search-form button').click(); page.locator('#customer-results').get_by_role('button',name='انتخاب',exact=True).click()
        expect(page.locator('#wallet-balance')).to_contain_text(f"{int(prior):,}".translate(str.maketrans('0123456789,','۰۱۲۳۴۵۶۷۸۹٬')),timeout=25000)
        page.locator('#credit-amount').fill('۱۰٬۰۰۰'); page.locator('#cashback-percent').fill('7.5'); page.locator('#credit-note').fill('آزمون مشتری موجود افرند '+run)
        with page.expect_response('**/api/wallet/credits',timeout=60000) as pending:
            page.locator('#submit-credit').click(); page.locator('#confirm-accept').click()
        existing_credit=pending.value.json(); assert pending.value.ok and not existing_credit['replayed'],existing_credit
        expect(page.locator('#credit-result')).to_contain_text('به کیف پول واریز شد',timeout=45000)
        balance=woo.request('GET',f"afrand-wallet/customers/{existing['customer_id']}")['wallet']['balance']; assert float(balance)==prior+750
        report['transactions'].append(existing_credit['transaction_id']); passed('existing_wordpress_customer_credit_native_balance_readback')

        sample=workbook([[f'LIVE-{run}-1','آزمایشی کیف پول',existing['phone'],5000,'تومان'],[f'LIVE-{run}-2','آزمایشی اکسل افرند '+run,phones[1],10000,'تومان'],[f'LIVE-{run}-BAD','ردیف خطا','123',-1,'تومان']]).getvalue()
        page.locator('#tab-import').click()
        file={'name':'wallet-live.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':sample}
        page.locator('#excel-file').set_input_files(file); page.locator('#preview-file').click()
        expect(page.locator('#preview-summary')).to_contain_text('۲ واریز معتبر',timeout=45000)
        expect(page.locator('#preview-summary')).to_contain_text('۱ ردیف نیازمند اصلاح')
        expect(page.locator('#preview-summary')).to_contain_text('۷۵۰ تومان')
        page.locator('#commit-import').click(); page.locator('#confirm-cancel').click()
        with page.expect_response('**/api/import/commit',timeout=90000) as pending:
            page.locator('#commit-import').click(); page.locator('#confirm-accept').click()
        imported=pending.value.json(); assert pending.value.ok and imported['created']==2 and imported['needs_review']==0,imported
        (out/'import-results.json').write_text(json.dumps(imported,ensure_ascii=False,indent=2),encoding='utf-8')
        expect(page.locator('#import-result')).to_contain_text('واریز تازه: 2',timeout=45000)
        report['transactions'] += [row['transaction_id'] for row in imported['results']]
        new_id=next(row['customer_id'] for row in imported['results'] if row['customer_id']!=existing['customer_id']); report['customers'].append(new_id)
        new=woo.request('GET',f"customers/{new_id}"); assert new['email']=='' and new['billing']['phone']==phones[1]
        for row in imported['results']:
            native_t=woo.request('GET',f"wallet/{row['transaction_id']}"); assert float(native_t['amount'])==float(row['amount']) and int(native_t['user_id'])==row['customer_id']
        file['name']='renamed.xlsx'; page.locator('#excel-file').set_input_files(file); page.locator('#preview-file').click(); expect(page.locator('#commit-import')).to_be_enabled(timeout=45000)
        page.locator('#commit-import').click(); page.locator('#confirm-accept').click(); expect(page.locator('#import-result')).to_contain_text('قبلاً واریز شده: 2',timeout=90000)
        existing_wallet=woo.request('GET',f"afrand-wallet/customers/{existing['customer_id']}")['wallet']; assert float(existing_wallet['balance'])==prior+1000
        new_wallet=woo.request('GET',f"afrand-wallet/customers/{new_id}")['wallet']; assert float(new_wallet['balance'])==500 and len(new_wallet['transactions'])==1
        for customer_id in report['customers']: assert not woo.request('GET','orders',params={'customer':customer_id,'per_page':10})
        passed('excel_existing_new_invalid_reupload_no_double_credit_native_readback_no_orders')
        with page.expect_download() as download: page.get_by_role('button',name='دریافت گزارش واریز').click()
        download.value.save_as(str(out/'wallet-report.tsv'))
        page.set_viewport_size({'width':390,'height':844}); assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'); page.screenshot(path=str(out/'mobile-import.png'),full_page=True)
        page.locator('#tab-manual').click(); page.screenshot(path=str(out/'mobile-wallet.png'),full_page=True)
        assert not native and not report['javascript_errors'],(native,report['javascript_errors'])
        page.get_by_role('button',name='خروج',exact=True).click(); expect(page.locator('h1')).to_have_text('ورود به پنل')
        passed('report_download_mobile_rtl_no_javascript_errors_and_logout')
        browser.close()
    report['synthetic_credits_total']=2500
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('instance/verification/wallet-live/latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__': main()
