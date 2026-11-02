"""Real Chromium typing, caret, font and monetary payload regression checks.

Uses an isolated fake wallet; never contacts WordPress or a real customer.
"""
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
from flask import request
from test_wallet import FakeWallet
from browser_excel_batches import QuietHandler


def check_input(page, selector):
    field = page.locator(selector)
    field.fill('')
    field.press_sequentially('1234567')
    expect(field).to_have_value('1,234,567')
    assert field.evaluate('(e)=>e.selectionStart') == 9
    for value in ('۱۲۳۴۵۶۷', '١٢٣٤٥٦٧', '۱٬۲۳۴٬۵۶۷'):
        field.fill(value)
        expect(field).to_have_value('1,234,567')
    field.evaluate('(e)=>e.setSelectionRange(4,4)')
    field.press('8')
    expect(field).to_have_value('12,384,567')
    assert field.evaluate('(e)=>e.selectionStart') == 5
    field.fill('1234')
    field.evaluate('(e)=>e.setSelectionRange(2,2)')
    field.press('Backspace')
    expect(field).to_have_value('234')
    field.fill('1234')
    field.evaluate('(e)=>e.setSelectionRange(1,1)')
    field.press('Delete')
    expect(field).to_have_value('134')
    field.fill('1234567')
    field.evaluate('(e)=>e.setSelectionRange(2,5)')
    field.press('9')
    expect(field).to_have_value('19,567')
    for value in ('-1000', '10.50', 'abc'):
        field.fill(value)
        expect(field).to_have_value(value)
    field.fill('0')
    expect(field).to_have_value('0')
    field.fill('')
    expect(field).to_have_value('')
    field.fill('1234567')


def main():
    wallet, errors, payloads = FakeWallet(), [], []
    with tempfile.TemporaryDirectory() as folder:
        app = create_app({'TESTING': True, 'WALLET_MODE': True,
                          'DATABASE': str(Path(folder)/'wallet.db'),
                          'WC_WRITES_ENABLED': True, 'PANEL_AUTH_ENABLED': False}, woo_client=wallet)
        import_amounts = []
        @app.before_request
        def capture_import_amount():
            if request.path == '/api/import/preview':
                import_amounts.append(request.form.get('fixed_amount'))
        server = make_server('127.0.0.1', 5072, app, threaded=True, request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: payloads.append(request.post_data_json)
                        if request.url.endswith('/api/wallet/credits') else None)
                page.goto('http://127.0.0.1:5072', wait_until='networkidle')
                expect(page.locator('#connection-state')).to_contain_text('واریز فعال')
                page.evaluate('document.fonts.ready')
                for selector in ('body', '#customer-query', '#fixed-amount'):
                    assert page.locator(selector).evaluate('(e)=>getComputedStyle(e).fontFamily').startswith('Vazirmatn')
                page.locator('#customer-query').fill('09121234567')
                page.locator('#customer-search-form button').click()
                page.locator('#customer-results button').click()
                check_input(page, '#credit-amount')
                expect(page.locator('#cashback-summary')).to_contain_text('۶۱٬۷۲۸')
                page.locator('#submit-credit').click()
                page.locator('#confirm-accept').click()
                expect(page.locator('#credit-result')).to_be_visible()
                assert wallet.transactions[-1]['amount'] == '61728', wallet.transactions
                assert payloads[-1]['purchase_amount'] == '1234567', payloads
                page.locator('#tab-import').click()
                page.locator('#amount-mode').select_option('fixed')
                check_input(page, '#fixed-amount')
                wb = Workbook()
                wb.active.append(['نام', 'شماره', 'شرکت', 'مبلغ خرید'])
                wb.active.append(['مریم', '09121234567', '', 1000])
                file = Path(folder)/'amounts.xlsx'
                wb.save(file)
                page.locator('#excel-file').set_input_files(str(file))
                with page.expect_response('**/api/import/preview') as response:
                    page.locator('#preview-file').click()
                preview = response.value.json()
                assert preview['fixed_amount'] == '1234567', preview
                assert import_amounts == ['1234567'], import_amounts
                page.locator('#commit-import').click()
                page.locator('#confirm-accept').click()
                expect(page.locator('#import-result')).to_contain_text('نیازمند بررسی: 0')
                assert wallet.transactions[-1]['amount'] == '1234567', wallet.transactions
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print(json.dumps({'manual_purchase': '1234567', 'cashback': '61728',
                      'fixed_credit': '1234567', 'page_errors': errors}))


if __name__ == '__main__':
    main()
