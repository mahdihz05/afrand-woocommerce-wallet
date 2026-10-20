"""Wallet browser acceptance against a disposable backend; no external writes."""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from panel.app import create_app
from test_wallet import FakeWallet, workbook


def main():
    out=Path('instance/verification/wallet-ui'); out.mkdir(parents=True,exist_ok=True)
    checks=[]; errors=[]; native=[]
    with tempfile.TemporaryDirectory() as folder:
        woo=FakeWallet()
        app=create_app({'TESTING':True,'WALLET_MODE':True,'DATABASE':str(Path(folder)/'test.db'),
            'WC_BASE_URL':woo.base_url,'WC_STAGING_HOST':'www.wprokad.ir','WC_CONSUMER_KEY':'test','WC_CONSUMER_SECRET':'test','WC_WRITES_ENABLED':True},woo_client=woo)
        server=make_server('127.0.0.1',5056,app,threaded=True); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1366,'height':950},reduced_motion='reduce')
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('dialog',lambda dialog:(native.append(dialog.type),dialog.dismiss()))
                page.goto('http://127.0.0.1:5056',wait_until='networkidle')
                expect(page.locator('#connection-state')).to_contain_text('واریز فعال')
                assert page.locator('input[type=email]').count()==0
                page.screenshot(path=str(out/'desktop.png'),full_page=True)
                page.locator('#customer-query').fill('۰۹۱۲۱۲۳۴۵۶۷')
                page.evaluate("""() => { const original=fetch; window.fetch=async(...args)=>{ if(String(args[0]).startsWith('/api/customers?')) await new Promise(resolve=>window.releaseSearch=resolve); return original(...args); }; }""")
                page.locator('#customer-search-form button').click()
                expect(page.locator('#request-progress')).to_be_visible()
                expect(page.locator('#customer-search-form button')).to_have_attribute('aria-busy','true')
                page.screenshot(path=str(out/'search-loading.png'),full_page=True)
                page.evaluate('window.releaseSearch()')
                page.locator('#customer-results').get_by_role('button',name='انتخاب',exact=True).click()
                expect(page.locator('#wallet-balance')).to_contain_text('۰ تومان')
                expect(page.locator('#request-progress')).to_be_hidden()
                checks.append('existing_customer_normalized_phone_wallet_balance_loading')

                expect(page.locator('#cashback-percent')).to_have_value('5')
                page.locator('#credit-amount').fill('۲۵٬۰۰۰')
                expect(page.locator('#cashback-summary')).to_contain_text('۱٬۲۵۰ تومان')
                page.locator('#submit-credit').click()
                expect(page.locator('#confirm-title')).to_have_text('تأیید مبلغ خرید کاربر')
                expect(page.locator('#confirm-description')).to_contain_text('۱٬۲۵۰ تومان')
                page.locator('#confirm-cancel').click(); assert not woo.transactions
                expect(page.locator('#submit-credit')).to_be_focused()
                page.locator('#submit-credit').click(); page.keyboard.press('Escape'); assert not woo.transactions
                page.locator('#submit-credit').click()
                page.set_viewport_size({'width':390,'height':844}); page.screenshot(path=str(out/'confirmation-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1366,'height':950})
                def lose_response(route):
                    response=route.fetch(); assert response.ok
                    route.abort('failed')
                page.route('**/api/wallet/credits',lose_response,times=1)
                page.locator('#confirm-accept').click()
                expect(page.locator('#pending-credit')).to_be_visible()
                expect(page.locator('#global-message')).to_contain_text('ارتباط')
                assert len(woo.transactions)==1
                page.reload(wait_until='networkidle')
                expect(page.locator('#pending-credit')).to_be_visible()
                expect(page.locator('#retry-credit')).to_be_enabled()
                page.locator('#retry-credit').click()
                expect(page.locator('#credit-result')).to_contain_text('دوباره واریز نشد')
                expect(page.locator('#wallet-balance')).to_contain_text('۱٬۲۵۰ تومان')
                assert len(woo.transactions)==1
                checks.append('credit_cancel_escape_lost_response_reload_reconcile_no_double_credit')

                page.locator('#show-new-customer').click()
                for key,value in {'first_name':'ندا','last_name':'نمونه','phone':'۰۹۱۲۳۳۳۴۴۵۵'}.items(): page.locator(f'#new-customer-form [name={key}]').fill(value)
                page.locator('#save-customer-button').click(); expect(page.locator('#confirm-title')).to_have_text('ثبت مشتری'); page.locator('#confirm-accept').click()
                expect(page.locator('#selected-customer')).to_contain_text('ندا')
                expect(page.locator('#wallet-balance')).to_contain_text('۰ تومان')
                assert len(woo.customers)==2 and 'email' not in woo.customers[-1]
                page.locator('#credit-amount').fill('۱۰٬۰۰۰'); page.locator('#cashback-percent').fill('۷٫۵'); page.locator('#submit-credit').click(); page.locator('#confirm-accept').click()
                expect(page.locator('#credit-result')).to_contain_text('به کیف پول واریز شد')
                expect(page.locator('#wallet-balance')).to_contain_text('۷۵۰ تومان')
                page.locator('#wallet-history summary').click()
                expect(page.locator('#wallet-transactions')).to_contain_text('۷۵۰ تومان')
                expect(page.locator('#cashback-percent')).to_have_value('5')
                checks.append('new_customer_no_email_custom_cashback_native_history')

                page.locator('#tab-import').click()
                sample=workbook([['UI-W1','مریم','09121234567',1000,'IRT'],['UI-W2','ندا','09123334455',500,'IRT'],['UI-W3','ندا','09123334455',250,'IRT'],['BAD','خطا','123',-10,'IRT']]).getvalue()
                page.locator('#excel-file').set_input_files({'name':'wallet.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':sample})
                page.locator('#preview-file').click()
                expect(page.locator('#preview-summary')).to_contain_text('۳ واریز معتبر')
                page.locator('#only-errors').check(); expect(page.locator('#preview-rows tr')).to_have_count(1)
                page.locator('#only-errors').uncheck()
                page.locator('#commit-import').click(); page.locator('#confirm-cancel').click(); assert len(woo.transactions)==2
                page.locator('#commit-import').click(); page.locator('#confirm-accept').click()
                expect(page.locator('#import-result')).to_contain_text('واریز تازه: 3')
                assert len(woo.transactions)==5
                with page.expect_download() as download: page.get_by_role('button',name='دریافت گزارش واریز').click()
                download.value.save_as(str(out/'report.tsv'))
                for attempt in range(1):
                    page.locator('#excel-file').set_input_files({'name':'renamed.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':sample})
                    page.locator('#preview-file').click(); expect(page.locator('#commit-import')).to_be_enabled()
                    page.locator('#commit-import').click(); page.locator('#confirm-accept').click()
                    expect(page.locator('#import-result')).to_contain_text('قبلاً واریز شده: 3')
                assert len(woo.transactions)==5
                assert [t['amount'] for t in woo.transactions]==['1250','750','50','25','12']
                page.locator('#preview-file').click(); expect(page.locator('#commit-import')).to_be_enabled()
                page.locator('#import-cashback-percent').fill('10'); expect(page.locator('#import-preview')).to_be_hidden()
                expect(page.locator('#commit-import')).to_be_disabled()
                checks.append('excel_cashback_preview_errors_cancel_commit_download_repeat_and_rate_invalidation')

                custom=workbook([['MAPPED','ندا','09123334455',50]],headers=['سند فروش','طرف','تماس مشتری','افزایش اعتبار']).getvalue()
                page.locator('#excel-file').set_input_files({'name':'export.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':custom})
                page.locator('#preview-file').click(); expect(page.locator('#column-mapping')).to_be_visible()
                for key,value in {'reference':'0','customer_name':'1','phone':'2','amount':'3'}.items(): page.locator(f'#mapping-fields [name={key}]').select_option(value)
                page.locator('#mapping-form button').click(); expect(page.locator('#preview-summary')).to_contain_text('۱ واریز معتبر')
                checks.append('accounting_custom_column_mapping')

                for width in (390,768,1366):
                    page.set_viewport_size({'width':width,'height':950})
                    for view in ('manual','import'):
                        page.locator(f'#tab-{view}').click(); assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        page.screenshot(path=str(out/f'{view}-{width}.png'),full_page=True)
                checks.append('mobile_tablet_desktop_rtl_no_overflow')
                assert not errors and not native,(errors,native)
                assert all(path.startswith('afrand-wallet/') for _,path in woo.calls)
                browser.close()
        finally: server.shutdown(); thread.join(timeout=3); server.server_close()
    report={'checks':checks,'javascript_errors':errors,'native_dialogs':native,'external_writes':0}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__': main()
