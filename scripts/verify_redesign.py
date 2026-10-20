"""Exercise the redesigned UI against a disposable FakeWoo server, without store writes."""

import io
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from panel.app import create_app
from test_app import FakeWoo


def main():
    output = Path('instance/verification/redesign')
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    checks = []
    with tempfile.TemporaryDirectory() as folder:
        woo = FakeWoo()
        app = create_app({'TESTING': True, 'DATABASE': str(Path(folder) / 'test.db'),
            'WC_BASE_URL': 'https://www.wprokad.ir', 'WC_STAGING_HOST': 'www.wprokad.ir',
            'WC_CONSUMER_KEY': 'test', 'WC_CONSUMER_SECRET': 'test', 'WC_WRITES_ENABLED': True}, woo_client=woo)
        server = make_server('127.0.0.1', 5056, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1366, 'height': 950}, reduced_motion='reduce')
                page.on('pageerror', lambda error: errors.append(str(error)))
                native_dialogs = []
                page.on('dialog', lambda dialog: (native_dialogs.append(dialog.type), dialog.dismiss()))
                page.goto('http://127.0.0.1:5056', wait_until='networkidle')
                expect(page.locator('#connection-state')).to_contain_text('ثبت فعال')
                expect(page.locator('#customer-card')).to_be_visible()
                expect(page.locator('#products-card')).to_be_hidden()
                expect(page.locator('#step-review')).to_be_disabled()
                page.screenshot(path=str(output / 'desktop-start.png'), full_page=True)
                page.locator('#customer-query').fill('09121234567')
                page.evaluate("""() => {
                  const original = window.fetch;
                  window.fetch = async (...args) => {
                    if (String(args[0]).startsWith('/api/customers?')) await new Promise(resolve => window.releaseSearch = resolve);
                    return original(...args);
                  };
                }""")
                page.locator('#customer-search-form button').click()
                expect(page.locator('#request-progress')).to_be_visible()
                expect(page.locator('#customer-search-form button')).to_have_attribute('aria-busy', 'true')
                page.screenshot(path=str(output / 'search-loading.png'), full_page=True)
                page.evaluate('window.releaseSearch()')
                expect(page.locator('#request-progress')).to_be_hidden()
                page.locator('#customer-results').get_by_role('button', name='انتخاب', exact=True).click()
                expect(page.locator('#customer-card')).to_be_hidden()
                expect(page.locator('#products-card')).to_be_visible()
                page.locator('#product-results').get_by_role('button', name='افزودن').click()
                page.locator('.qty-control').fill('2')
                page.locator('.qty-control').dispatch_event('change')
                expect(page.locator('#purchase-receipt')).to_contain_text('۲ عدد')
                page.screenshot(path=str(output / 'desktop-products.png'), full_page=True)
                page.locator('#review-purchase').click()
                expect(page.locator('#products-card')).to_be_hidden()
                expect(page.locator('#review-card')).to_be_visible()
                expect(page.locator('#review-summary')).to_contain_text('۲ عدد')
                page.locator('#back-products').click()
                expect(page.locator('.qty-control')).to_have_value('2')
                page.locator('#back-customer').click()
                expect(page.locator('#selected-customer')).to_contain_text('مریم')
                page.locator('#step-review').click()
                page.screenshot(path=str(output / 'desktop-review.png'), full_page=True)
                page.locator('#submit-order').click()
                expect(page.locator('#confirm-dialog')).to_be_visible()
                expect(page.locator('#confirm-title')).to_have_text('تأیید ثبت خرید حضوری')
                expect(page.locator('#confirm-cancel')).to_be_focused()
                page.locator('#confirm-cancel').click()
                assert not woo.created_orders
                expect(page.locator('#submit-order')).to_be_focused()
                page.locator('#submit-order').click()
                page.keyboard.press('Escape')
                assert not woo.created_orders
                page.locator('#submit-order').click()
                page.set_viewport_size({'width':390,'height':844})
                assert page.locator('#confirm-dialog').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                page.screenshot(path=str(output / 'confirmation-mobile.png'), full_page=True)
                page.set_viewport_size({'width':1366,'height':950})
                page.screenshot(path=str(output / 'confirmation-desktop.png'), full_page=True)
                page.locator('#confirm-accept').click()
                expect(page.locator('#order-result')).to_contain_text('900')
                expect(page.locator('#customer-card')).to_be_visible()
                assert woo.created_orders[0]['line_items'][0]['quantity'] == 2
                checks.append('manual_steps_back_navigation_receipt_and_commit')
                page.locator('#order-result button').click()
                expect(page.locator('#customer-details')).to_be_visible()
                expect(page.locator('#customer-edit-form')).to_be_hidden()
                page.locator('.edit-details summary').click()
                expect(page.locator('#customer-edit-form')).to_be_visible()
                checks.append('customer_history_and_collapsed_edit')
                page.locator('#show-new-customer').click()
                form = page.locator('#new-customer-form')
                for field, value in {'first_name':'سارا', 'last_name':'نمونه', 'phone':'09129998877', 'email':'redesign@example.test'}.items():
                    form.locator(f'[name={field}]').fill(value)
                form.locator('button[type=submit]').click()
                expect(page.locator('#products-card')).to_be_visible()
                expect(page.locator('#purchase-receipt')).to_contain_text('سارا')
                checks.append('direct_new_customer_entry')

                # Unknown column names exercise the real parser and mapping UI together.
                workbook = Workbook()
                sheet = workbook.active
                sheet.append(['گزارش حسابداری'])
                sheet.append(['سند فروش','خریدار','تماس','شناسه','مقدار فروش','مبلغ','واحد پول'])
                sheet.append(['UI-ACC-1','مریم','09121234567','SKU-1',2,240000,'تومان'])
                sheet.append(['UI-ACC-2','مریم','09121234567','NOT-FOUND',1,50000,'تومان'])
                data = io.BytesIO(); workbook.save(data)
                page.locator('#tab-import').click()
                page.screenshot(path=str(output / 'desktop-import.png'), full_page=True)
                page.locator('#excel-file').set_input_files({'name':'export.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':data.getvalue()})
                page.locator('#import-form button').click()
                expect(page.locator('#column-mapping')).to_be_visible()
                page.locator('#header-row').select_option('2')
                for key, index in {'order_ref':0,'customer_name':1,'customer_phone':2,'product_sku':3,'quantity':4,'line_total':5,'currency':6}.items():
                    page.locator(f'#mapping-fields [name={key}]').select_option(str(index))
                page.screenshot(path=str(output / 'desktop-mapping.png'), full_page=True)
                page.locator('#mapping-form button').click()
                expect(page.locator('#import-preview')).to_be_visible()
                expect(page.locator('#preview-rows tr')).to_have_count(2)
                expect(page.locator('#preview-rows')).to_contain_text('240000 تومان')
                page.locator('#only-errors').check()
                expect(page.locator('#preview-rows tr')).to_have_count(1)
                expect(page.locator('#preview-rows')).to_contain_text('NOT-FOUND')
                page.locator('#only-errors').uncheck()
                page.screenshot(path=str(output / 'desktop-preview.png'), full_page=True)
                checks.append('custom_header_mapping_and_error_filter')
                page.locator('#edit-mapping').click()
                expect(page.locator('#commit-import')).to_be_disabled()
                page.locator('#mapping-form button').click()
                expect(page.locator('#commit-import')).to_be_enabled()
                page.locator('#commit-import').click()
                expect(page.locator('#confirm-title')).to_have_text('تأیید ثبت فاکتورها')
                page.locator('#confirm-accept').click()
                expect(page.locator('#import-result')).to_contain_text('ثبت‌شده: 1')
                assert len(woo.created_orders) == 2
                assert any(m['key'] == 'afrand_accounting_export' for m in woo.created_orders[-1]['meta_data'])
                with page.expect_download() as pending:
                    page.locator('#import-result').get_by_role('button', name='دریافت گزارش ثبت').click()
                pending.value.save_as(str(output / 'result.tsv'))
                assert 'UI-ACC-2' in (output / 'result.tsv').read_text(encoding='utf-8-sig')
                checks.append('mapping_revision_commit_and_report')

                page.locator('#tab-manual').click()
                page.locator('#step-customer').click()
                page.locator('#show-new-customer').click()
                for field, value in {'first_name':'ندا', 'last_name':'نمونه', 'phone':'09123334455', 'email':'popup@example.test'}.items():
                    page.locator(f'#new-customer-form [name={field}]').fill(value)
                page.locator('#save-customer-button').click()
                expect(page.locator('#confirm-title')).to_have_text('ثبت مشتری جدید')
                page.locator('#confirm-accept').click()
                expect(page.locator('#customer-detail-name')).to_contain_text('ندا')
                if not page.locator('#customer-edit-form').is_visible():
                    page.locator('.edit-details summary').click()
                page.locator('#customer-edit-form [name=first_name]').fill('ندا ویرایش')
                page.locator('#update-customer-button').click()
                expect(page.locator('#confirm-title')).to_have_text('ذخیرهٔ اطلاعات مشتری')
                page.locator('#confirm-accept').click()
                expect(page.locator('#customer-detail-name')).to_contain_text('ندا ویرایش')
                assert len(woo.created_customers) == 1 and len(woo.updated_customers) == 1
                checks.append('customer_create_and_update_custom_confirmations')

                page.locator('#step-products').click()
                page.wait_for_load_state('networkidle')
                page.evaluate("""() => {
                  const original = window.fetch;
                  window.heldRequests = [];
                  window.fetch = async (...args) => {
                    await new Promise(resolve => window.heldRequests.push(resolve));
                    return original(...args);
                  };
                }""")
                page.locator('#refresh-connection').click()
                page.locator('#product-search').click()
                expect(page.locator('#refresh-connection')).to_have_attribute('aria-busy', 'true')
                expect(page.locator('#product-search')).to_have_attribute('aria-busy', 'true')
                page.evaluate('window.heldRequests.shift()()')
                expect(page.locator('#refresh-connection')).not_to_have_attribute('aria-busy', 'true')
                expect(page.locator('#request-progress')).to_be_visible()
                page.route('**/api/products?*', lambda route: route.fulfill(status=500,content_type='application/json',body='{"error":"test failure"}'), times=1)
                page.evaluate('window.heldRequests.shift()()')
                expect(page.locator('#global-message')).to_contain_text('test failure')
                expect(page.locator('#request-progress')).to_be_hidden()
                expect(page.locator('#product-search')).not_to_have_attribute('aria-busy', 'true')
                checks.append('concurrent_loading_and_error_cleanup')

                for width in (390, 768, 1366):
                    page.set_viewport_size({'width':width,'height':900})
                    for tab in ('manual','import'):
                        page.locator(f'#tab-{tab}').click()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow {width} {tab}'
                        page.screenshot(path=str(output / f'{tab}-{width}.png'), full_page=True)
                checks.append('mobile_tablet_desktop_no_overflow')
                assert not errors, errors
                assert not native_dialogs, native_dialogs
                checks.append('custom_confirmation_cancel_escape_focus_mobile_and_loading')
                browser.close()
        finally:
            server.shutdown(); thread.join(timeout=3); server.server_close()
    report = {'checks':checks,'browser_errors':errors,'external_writes':0}
    (output / 'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__ == '__main__':
    main()
