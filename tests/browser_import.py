"""Browser acceptance. Default: isolated synthetic wallet on loopback.

--live explicitly credits ONLY the existing academy acceptance customer by
50 + 25 native units. Requires the configured local app at :5055.
Screenshots and receipts go to tmp/browser-import[-live], never credentials.
"""
import argparse
import json
import os
import secrets
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook, load_workbook
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--replay-existing', action='store_true', help='Recheck the previous live files without new credits')
    parser.add_argument('--base-url', help='Hosted acceptance; requires --live --replay-existing and PANEL_TEST_PASSWORD')
    args = parser.parse_args()
    assert not args.replay_existing, 'Use saved-import recovery to replay a fixed run; uploading again intentionally credits again'
    assert not args.replay_existing or args.live, '--replay-existing requires --live'
    assert not args.base_url or (args.live and args.replay_existing), 'Hosted checks only replay previously credited files'
    output = Path('tmp/browser-import-hosted' if args.base_url else 'tmp/browser-import-live' if args.live else 'tmp/browser-import')
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory()
    server = None
    if args.live:
        if not args.base_url:
            from run import app
        receipt = json.loads(Path('instance/academy-acceptance.json').read_text('utf-8'))
        cid, phone = receipt['customer_id'], receipt['phone']
        base = args.base_url.rstrip('/') if args.base_url else 'http://127.0.0.1:5055'
    else:
        from panel.app import create_app
        from test_wallet import FakeWallet
        wallet = FakeWallet()
        app = create_app({'TESTING': True, 'WALLET_MODE': True,
                          'DATABASE': str(Path(temporary.name) / 'wallet.db'),
                          'WC_WRITES_ENABLED': True, 'PANEL_AUTH_ENABLED': False}, woo_client=wallet)
        cid, phone = 7, '09121234567'
        server = make_server('127.0.0.1', 5068, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:5068'

    def file(name, headers, rows):
        path = output / name
        wb = Workbook()
        if headers:
            wb.active.append(headers)
        for row in rows:
            wb.active.append(row)
        wb.save(path)
        return str(path.resolve())

    errors, results = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1100}, accept_downloads=True)
        if args.live and not args.base_url:
            cookie = app.session_interface.get_signing_serializer(app).dumps({'panel_user': app.config['PANEL_USERNAME']})
            context.add_cookies([{'name': 'session', 'value': cookie, 'url': base, 'httpOnly': True, 'sameSite': 'Lax'}])
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        page.set_default_timeout(45000)
        if args.base_url:
            assert context.request.get(base + '/api/status').status == 401
            page.locator('#username').fill(os.environ.get('PANEL_TEST_USERNAME', 'admin'))
            page.locator('#password').fill('wrong-test-password')
            page.locator('form button[type=submit]').click()
            expect(page.get_by_role('alert')).to_be_visible()
            page.locator('#username').fill(os.environ.get('PANEL_TEST_USERNAME', 'admin'))
            page.locator('#password').fill(os.environ['PANEL_TEST_PASSWORD'])
            page.locator('form button[type=submit]').click()
            page.wait_for_url(base + '/')
        expect(page.locator('#connection-state')).to_contain_text('واریز فعال')
        page.locator('#tab-import').click()

        def balance():
            r = context.request.get(base + f'/api/customers/{cid}')
            assert r.ok, r.status
            return int(r.json()['wallet']['balance'])

        before = balance()
        page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        bad = output / 'broken.xlsx'
        bad.write_text('not an excel workbook', 'utf-8')
        page.locator('#excel-file').set_input_files(str(bad.resolve()))
        page.locator('#preview-file').click()
        expect(page.locator('#import-feedback')).to_contain_text('معتبر نیست')
        assert balance() == before

        headers = ['شماره سند', 'نام', 'شماره', 'شرکت', 'مبلغ خرید']
        replay_source = Path('tmp/browser-import-live') if args.base_url else output
        ref = (load_workbook(replay_source / 'mixed.xlsx').active.cell(2, 1).value
               if args.replay_existing else 'browser-' + secrets.token_hex(8))
        cashback_delta, fixed_delta = (0, 0) if args.replay_existing else (50, 25)
        mixed = file('mixed.xlsx', headers, [[ref, 'آزمایش تحویل افرند', phone, 'شرکت فایل', 1000],
                                             [ref + '-bad', 'ردیف نامعتبر', '123', '', 1000]])
        page.locator('#excel-file').set_input_files(mixed)
        page.locator('#import-cashback-percent').fill('101')
        page.locator('#preview-file').click()
        expect(page.locator('#import-feedback')).to_contain_text('۱۰۰')
        page.locator('#import-cashback-percent').fill('5')

        def preview():
            with page.expect_response('**/api/import/preview', timeout=90000) as response:
                page.locator('#preview-file').click()
            data = response.value.json()
            assert response.value.ok, data
            expect(page.locator('#import-preview')).to_be_visible()
            return data

        data = preview()
        assert (data['valid_count'], data['invalid_count'], data['total']) == (1, 1, '50'), data
        assert balance() == before
        page.locator('#only-errors').check()
        expect(page.locator('#preview-rows tr')).to_have_count(1)
        page.locator('#only-errors').uncheck()
        expect(page.locator('#preview-rows tr')).to_have_count(2)
        page.locator('#commit-import').click()
        expect(page.locator('#confirm-description')).to_contain_text('۵۰')
        page.locator('#confirm-dialog button[value="cancel"]').click()
        assert balance() == before

        def commit():
            page.locator('#commit-import').click()
            with page.expect_response('**/api/import/commit', timeout=90000) as response:
                page.locator('#confirm-accept').click()
            result = response.value.json()
            expect(page.locator('#import-result')).to_be_visible(timeout=90000)
            results.append(result)
            return result

        result = commit()
        assert result['replayed' if args.replay_existing else 'created'] == 1 and result['needs_review'] == 0, result
        assert balance() == before + cashback_delta
        with page.expect_download() as download:
            page.locator('#import-result button').click()
        report = download.value.path().read_bytes().decode('utf-8-sig')
        assert 'شماره سند' in report and ref in report

        # Re-upload, not just re-click: protected identity must survive new draft.
        preview()
        assert commit()['replayed'] == 1
        assert balance() == before + cashback_delta

        # Fixed, headerless, invalid purchase amount: full fixed credit only.
        fixed = file('fixed-no-header.xlsx', None, [['آزمایش تحویل افرند', phone, '', 'ignored']])
        page.locator('#excel-file').set_input_files(fixed)
        page.locator('#amount-mode').select_option('fixed')
        expect(page.locator('#confirm-dialog')).not_to_be_visible()
        expect(page.locator('#import-cashback-percent')).not_to_be_visible()
        page.locator('#fixed-amount').fill('0')
        page.locator('#preview-file').click()
        expect(page.locator('#import-feedback')).to_be_visible()
        page.locator('#fixed-amount').fill('25')
        data = preview()
        assert data['header_row'] == 0 and data['total'] == '25' and data['cashback_percent'] == '0'
        assert balance() == before + cashback_delta
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile page overflows'
        page.locator('#import-preview').screenshot(path=str(output / 'mobile-preview.png'))
        assert commit()['replayed' if args.replay_existing else 'created'] == 1
        assert balance() == before + cashback_delta + fixed_delta

        # Restore latest saved draft after reload, without any write.
        page.reload()
        page.locator('#tab-import').click()
        page.locator('.saved-imports-panel summary').click()
        page.locator('#saved-imports button').first.click()
        expect(page.locator('#import-preview')).to_be_visible()
        expect(page.locator('#amount-mode')).to_have_value('fixed')
        expect(page.locator('#fixed-amount')).to_have_value('25')
        assert balance() == before + cashback_delta + fixed_delta

        # Changing inputs invalidates the restored preview and confirmation.
        page.locator('#fixed-amount').fill('26')
        expect(page.locator('#import-preview')).not_to_be_visible()
        expect(page.locator('#commit-import')).to_be_disabled()
        page.locator('#excel-file').set_input_files(fixed)
        page.route('**/api/import/preview', lambda route: route.abort())
        page.locator('#preview-file').click()
        expect(page.locator('#import-feedback')).to_contain_text('ارتباط')
        page.unroute('**/api/import/preview')
        assert balance() == before + cashback_delta + fixed_delta
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        assert not errors, errors
        receipt = {'url': base, 'hosted': bool(args.base_url), 'live': args.live, 'replay_existing': args.replay_existing, 'customer_id': cid, 'balance_before': before,
                   'balance_after': balance(), 'credits': results, 'page_errors': errors,
                   'checks': ['corrupt file', 'invalid percent', 'preview no writes', 'invalid row filter',
                              'cancel no writes', 'cashback credit', 'report download', 'reupload replay',
                              'fixed headerless', 'zero fixed amount', 'mobile no overflow',
                              'saved draft restore', 'changed amount invalidates preview', 'network failure no writes']}
        (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps({'passed': len(receipt['checks']), 'live': args.live,
                          'before': before, 'after': receipt['balance_after'], 'page_errors': errors}))
        browser.close()
    if server:
        server.shutdown()
    temporary.cleanup()


if __name__ == '__main__':
    main()
