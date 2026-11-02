"""Browser acceptance of direct-mobile and cashback-purchase multiplicity."""
import json
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from panel.app import create_app
from test_wallet import FakeWallet
from browser_excel_batches import QuietHandler


def main():
    output = Path('tmp/browser-duplicate-modes')
    output.mkdir(parents=True, exist_ok=True)
    wallet = FakeWallet()
    errors, receipts = [], []
    with tempfile.TemporaryDirectory() as folder:
        app = create_app({'TESTING': True, 'WALLET_MODE': True,
                          'DATABASE': str(Path(folder)/'wallet.db'),
                          'WC_WRITES_ENABLED': True, 'PANEL_AUTH_ENABLED': False}, woo_client=wallet)
        server = make_server('127.0.0.1', 5071, app, threaded=True, request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(accept_downloads=True)
                page.on('pageerror', lambda error: errors.append(str(error)))
                for mode in ('fixed', 'excel'):
                    rows = ([['مریم', '09121234567', '', 1000], ['دیگر', '09120000002', '', 1000],
                             ['مریم', '۰۹۱۲۱۲۳۴۵۶۷', '', 1000]] if mode == 'fixed' else
                            [['مریم', '09121234567', '', 1000, f'BROWSER-{i}'] for i in range(5)])
                    wb = Workbook(); wb.active.append(['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند'])
                    for row in rows: wb.active.append(row)
                    file = Path(folder)/(mode+'.xlsx'); wb.save(file)
                    before = len(wallet.transactions)
                    for run in range(3):
                        replay = run > 0 and mode == 'excel'
                        page.goto('http://127.0.0.1:5071')
                        page.wait_for_load_state('networkidle')
                        expect(page.locator('#connection-state')).to_contain_text('واریز فعال')
                        page.locator('#tab-import').click()
                        page.locator('#amount-mode').select_option(mode)
                        page.locator('#fixed-amount' if mode == 'fixed' else '#import-cashback-percent').fill('500' if mode == 'fixed' else '5')
                        expect(page.locator('#reference-help')).to_be_hidden() if mode == 'fixed' else expect(page.locator('#reference-help')).to_be_visible()
                        page.locator('#excel-file').set_input_files(str(file))
                        with page.expect_response('**/api/import/preview') as response:
                            page.locator('#preview-file').click()
                        preview = response.value.json()
                        assert preview['valid_count'] == (2 if mode == 'fixed' else 5), preview
                        assert preview['skipped_count'] == (1 if mode == 'fixed' else 0), preview
                        expect(page.locator('#preview-rows tr')).to_have_count(len(rows))
                        if mode == 'fixed':
                            expect(page.locator('#preview-summary')).to_contain_text('موبایل تکراری')
                            expect(page.locator('#preview-rows')).to_contain_text('فقط ردیف 2')
                        page.locator('#commit-import').click(); page.locator('#confirm-accept').click()
                        expect(page.locator('#import-result')).to_contain_text('نیازمند بررسی: 0')
                        expect(page.locator('#import-result')).to_contain_text('واریز تازه: 0' if replay else f"واریز تازه: {preview['valid_count']}")
                        assert len(wallet.transactions)-before == (2 * (run + 1) if mode == 'fixed' else 5)
                        expect(page.locator('#import-result button')).to_have_count(0)
                        if mode == 'fixed':
                            expect(page.locator('#import-result')).to_contain_text('موبایل تکراری')
                        for row in preview['invalid_rows'] + preview.get('skipped_rows', []):
                            expect(page.locator('#import-result')).to_contain_text(f"ردیف {row['row_number']}")
                        page.screenshot(path=str(output/f'{mode}-{replay}.png'), full_page=True)
                    receipts.append({'mode':mode,'input_rows':len(rows),'credits':len(wallet.transactions)-before,'reupload_new_credits':2 if mode == 'fixed' else 0})
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    result={'scenarios':receipts,'page_errors':errors}
    (output/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__': main()
