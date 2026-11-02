"""Isolated browser proof: every customer survives a lost batch response."""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from panel.app import create_app
from test_wallet import FakeWallet


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    output = Path('tmp/browser-excel-batches')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        wallet = FakeWallet()
        wallet.currency = 'IRR'
        app = create_app({'TESTING': True, 'WALLET_MODE': True,
                          'DATABASE': str(Path(folder) / 'wallet.db'),
                          'WC_WRITES_ENABLED': True, 'PANEL_AUTH_ENABLED': False}, woo_client=wallet)
        server = make_server('127.0.0.1', 5069, app, threaded=True, request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        receipts, errors = [], []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(accept_downloads=True)
                page.set_default_timeout(30000)
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = 'http://127.0.0.1:5069'
                for mode in ('excel', 'fixed'):
                    prefix = '0921' if mode == 'excel' else '0922'
                    rows = [[f'مشتری {i}', prefix + f'{i:07d}', 'شرکت آزمایش', 1001 + 100 * i, f'BATCH-{i}']
                            for i in range(7)]
                    # Include an existing customer, invalid first/last records, and a blank row.
                    wallet.customers.append({'id': 10000 + len(wallet.customers), 'first_name': 'موجود',
                                             'last_name': '', 'phone': rows[0][1]})
                    wb = Workbook()
                    wb.active.append(['نام', 'شماره', 'شرکت', 'مبلغ خرید', 'شماره سند'])
                    wb.active.append(['خطای اول', '123', '', 1000])
                    for row in rows:
                        wb.active.append(row)
                    wb.active.append([None] * 4)
                    wb.active.append(['خطای آخر', '', '', 1000])
                    file = Path(folder) / (mode + '.xlsx')
                    wb.save(file)
                    page.goto(base)
                    expect(page.locator('#connection-state')).to_contain_text('واریز فعال')
                    page.locator('#tab-import').click()
                    page.locator('#amount-mode').select_option(mode)
                    if mode == 'fixed':
                        page.locator('#fixed-amount').fill('37')
                    else:
                        page.locator('#import-cashback-percent').fill('7.5')
                    page.locator('#excel-file').set_input_files(str(file))
                    multi = Path(folder) / (mode + '-two-sheets.xlsx')
                    wb.create_sheet('برگهٔ دیگر').append(['مشتری دیگر', '09230000001', '', 1000])
                    wb.save(multi)
                    page.locator('#excel-file').set_input_files(str(multi))
                    page.locator('#preview-file').click()
                    expect(page.locator('#import-feedback')).to_contain_text('فایل چند برگه دارد')
                    page.locator('#excel-file').set_input_files(str(file))
                    with page.expect_response('**/api/import/preview') as response:
                        page.locator('#preview-file').click()
                    preview = response.value.json()
                    assert (preview['valid_count'], preview['invalid_count']) == (7, 2), preview
                    expect(page.locator('#preview-rows tr')).to_have_count(9)
                    before = len(wallet.transactions)
                    calls = []

                    def interrupt(route):
                        calls.append(route.request.post_data_json['offset'])
                        response = route.fetch()
                        assert response.ok
                        if len(calls) == 2:
                            # Server credited rows 3/4; client receives no response.
                            route.abort()
                        else:
                            route.fulfill(response=response)

                    page.route('**/api/import/commit', interrupt)
                    page.locator('#commit-import').click()
                    page.locator('#confirm-accept').click()
                    expect(page.locator('#import-feedback')).to_contain_text('ارتباط')
                    expect(page.locator('#commit-import')).to_be_enabled()
                    assert calls == [0, 2], calls
                    assert len(wallet.transactions) - before == 4
                    page.unroute('**/api/import/commit')

                    # Browser reload must recover progress from SQLite, not from JS state.
                    page.reload()
                    page.locator('#tab-import').click()
                    page.locator('.saved-imports-panel summary').click()
                    page.locator('#saved-imports button').first.click()
                    expect(page.locator('#import-preview')).to_be_visible()
                    page.locator('#commit-import').click()
                    page.locator('#confirm-accept').click()
                    expect(page.locator('#import-result')).to_be_visible()
                    expect(page.locator('#import-result')).to_contain_text('نیازمند بررسی: 0')
                    saved = page.request.get(base + '/api/import/' + preview['draft_id']).json()
                    assert saved['done'] and len(saved['results']) == 7, saved
                    assert len(wallet.transactions) - before == 7
                    for row in rows:
                        customer = next(c for c in wallet.customers if c['phone'] == row[1])
                        txs = [t for t in wallet.transactions if t['customer_id'] == customer['id']]
                        expected = 37 if mode == 'fixed' else row[3] * 75 // 1000
                        assert len(txs) == 1 and int(txs[0]['amount']) == expected, (row, txs)
                    expect(page.locator('#import-result button')).to_have_count(0)
                    for row in preview['valid_rows']:
                        expect(page.locator('#import-result')).to_contain_text(row['reference'])
                    for row in preview['invalid_rows']:
                        expect(page.locator('#import-result')).to_contain_text(f"ردیف {row['row_number']}")
                    page.screenshot(path=str(output / (mode + '.png')), full_page=True)
                    receipts.append({'mode': mode, 'valid': 7, 'invalid': 2, 'credited': 7,
                                     'interrupted_offsets': calls, 'resumed_offset': 4,
                                     'transactions_unique': len({r['transaction_id'] for r in saved['results']}),
                                     'visible_result_rows': 9, 'multiple_sheets_rejected': True,
                                     'total': preview['total']})
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
        (output / 'receipt.json').write_text(json.dumps({'checks': receipts, 'page_errors': errors},
                                                       ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps({'passed_modes': len(receipts), 'customers_verified': 14, 'page_errors': errors}))


if __name__ == '__main__':
    main()
